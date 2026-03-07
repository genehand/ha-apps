#!/usr/bin/env python3

import aiohttp
from aiohttp import web
import asyncio
import colorlog
import httpx
import json
import logging
import os
import re
import signal
import yaml
from dataclasses import dataclass, field
from typing import Dict, Set, List, Optional

# Setup Logging
handler = colorlog.StreamHandler()
handler.setFormatter(
    colorlog.ColoredFormatter(
        "[%(asctime)s] %(log_color)s%(levelname)s%(reset)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logger = colorlog.getLogger(__name__)
logger.addHandler(handler)
logger.setLevel("INFO")

# Load the addon configuration
config = {}
CONFIG_FILE_PATH = "/data/options.json"
try:
    with open(CONFIG_FILE_PATH, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    logger.error(
        "Home Assistant add-on configuration file not found: /data/options.json"
    )


# Use config or set default values
HA_HOST = config.get("ha_host", "homeassistant:8123")
TRANSPARENT = config.get("transparent", True)
PROXY_PORT = 8124


@dataclass
class ClientState:
    """Encapsulates all per-client state data."""

    client_ip: str
    lovelace_entities: Set[str] = field(default_factory=set)
    filter_rules: List[Dict] = field(default_factory=list)
    lovelace_config_id: Optional[int] = None
    subscribe_entities_id: Optional[int] = None
    all_states: Optional[Dict] = None


# Per-client state storage: maps WebSocket client -> ClientState
CLIENT_STATES: Dict[object, ClientState] = {}


def matches_filter(entity_id, filter):
    """Checks if an entity_id matches any of the provided wildcard filters."""

    # Use auto-entities regex's when found
    regex_check = re.search("^/(.+)/$", filter)

    if regex_check:
        if re.search(regex_check.group(1), entity_id):
            return True

    else:
        escaped_pattern = re.escape(filter).replace(r"\*", ".*")
        if re.fullmatch(escaped_pattern, entity_id):
            return True

    return False


def parse_lovelace_entities(data, entities_set, filter_rules_list):
    """
    Recursively finds, validates, and filters entity IDs in a Lovelace configuration dictionary.
    The 'entities_set' argument is the client-specific set to populate.
    """
    entity_pattern = re.compile(r"^[\w]+\.[\w]+$")
    template_pattern = re.compile(r"states\[['\"]([\w]+\.[\w]+)['\"]\]")

    def validate_and_add(entity_id):
        if isinstance(entity_id, str) and entity_pattern.match(entity_id):
            entities_set.add(entity_id)

    if isinstance(data, dict):
        # Handle custom:auto-entities card filters
        if data.get("type") == "custom:auto-entities":
            filter_config = data.get("filter", {})
            if "include" in filter_config and isinstance(
                filter_config["include"], list
            ):
                for rule in filter_config["include"]:
                    # Case 1: Rule is a string (e.g., "light.kitchen_*")
                    if isinstance(rule, str):
                        entities_set.add(rule)
                        continue

                    if not isinstance(rule, dict):
                        continue

                    # Case 2: Rule is a dict with 'entity_id' (e.g., {'entity_id': 'sensor.temp_*'})
                    entity_id_val = rule.get("entity_id")
                    if entity_id_val and isinstance(entity_id_val, str):
                        entities_set.add(entity_id_val)

                    # Case 3: Rule is a dict with complex filters like 'attributes', 'domain', etc.
                    complex_keys = [
                        "attributes",
                        "domain",
                        "group",
                        "integration",
                        "device",
                        "area",
                    ]
                    supported_filters = {
                        key: rule[key] for key in complex_keys if key in rule
                    }
                    if supported_filters:
                        filter_rules_list.append(supported_filters)
                    if "or" in rule:
                        for condition in rule["or"]:
                            filter_rules_list.append(condition)

        for key, value in data.items():
            if key == "entity":
                validate_and_add(value)
            elif key == "entities" and isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        validate_and_add(item)
                    elif isinstance(item, dict) and "entity" in item:
                        validate_and_add(item.get("entity"))

            # custom:bubble-card background color
            elif re.match(r"\d+_entity$", key):
                validate_and_add(value)

            if isinstance(value, str):
                matches = template_pattern.findall(value)
                for match in matches:
                    validate_and_add(match)
            elif isinstance(value, (dict, list)):
                # Recursively call with the client's specific entities set
                parse_lovelace_entities(value, entities_set, filter_rules_list)
    elif isinstance(data, list):
        for item in data:
            parse_lovelace_entities(item, entities_set, filter_rules_list)


def get_client_ip(request):
    """
    Determines the client's IP address by checking X-Forwarded-For header first.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # The first IP in the list is the original client's IP.
        return x_forwarded_for.split(",")[0].strip()
    return request.remote


def _check_attribute_match(state_attributes, rule_attributes):
    """
    Checks if an entity's attributes match the rule's attribute filters.
    Supports wildcard matching for attribute values.
    """
    if not isinstance(state_attributes, dict) or not isinstance(rule_attributes, dict):
        return False

    for key, value_pattern in rule_attributes.items():
        if key not in state_attributes:
            return False

        actual_value = state_attributes[key]

        if isinstance(value_pattern, str) and "*" in value_pattern:
            # Convert glob to regex for attribute value matching
            regex_pattern = re.escape(value_pattern).replace(r"\*", ".*")
            if not re.fullmatch(regex_pattern, str(actual_value)):
                return False
        elif isinstance(value_pattern, str):
            regex_check = re.fullmatch("/(.+)/", value_pattern)
            if regex_check:
                parsed_regex = regex_check.group(1)
                if not re.match(parsed_regex, str(actual_value)):
                    return False
            elif actual_value != value_pattern:
                return False

    return True


def _resolve_rules_and_update_entities(all_states, rules, entities_set):
    """
    Iterates through all known states and adds entity IDs to the client's
    set if they match any of the complex auto-entities filter rules.
    """
    count_before = len(entities_set)

    for entity_id, state_obj in all_states.items():
        for rule in rules:
            matches = True
            if "domain" in rule:
                if rule["domain"].endswith("/") and not matches_filter(
                    entity_id, rule["domain"]
                ):
                    matches = False
                elif not re.match(rule["domain"] + ".", entity_id):
                    matches = False

            if matches and "entity_id" in rule:
                if not matches_filter(entity_id, rule["entity_id"]):
                    matches = False

            if matches and "attributes" in rule:
                # The state object from subscribe_entities has attributes under 'a'
                if not _check_attribute_match(
                    state_obj.get("a", {}), rule["attributes"]
                ):
                    matches = False

            if matches:
                entities_set.add(entity_id)
                break

    added_count = len(entities_set) - count_before
    if added_count > 0:
        logger.debug(
            f"Resolved {added_count} new entities from auto-entities attribute/domain rules."
        )


def _entity_id_matches(entity_id, client_entities):
    """
    Checks if an entity_id matches any literal or wildcard pattern in the client's entity set.
    """
    # Fast check for literal matches
    if entity_id in client_entities:
        return True

    # Slower check for wildcard pattern matches
    for pattern in client_entities:
        if "*" in pattern:
            # Convert glob to regex. Note: This is a simple glob-to-regex.
            regex_pattern = re.escape(pattern).replace(r"\*", ".*")
            if re.fullmatch(regex_pattern, entity_id):
                return True
    return False


async def proxy_handler(request):
    client_ip = get_client_ip(request)
    is_websocket = (
        "Upgrade" in request.headers
        and request.headers["Upgrade"].lower() == "websocket"
    )

    if is_websocket:
        if request.path == "/api/websocket":
            logger.info(
                f"Filtered WebSocket connection for {request.path} from {client_ip}"
            )
            return await proxy_websocket_filtered(request, client_ip)
        else:
            logger.info(
                f"Standard WebSocket connection for {request.path} from {client_ip}"
            )
            return await proxy_websocket_transparent(request, client_ip)
    else:
        logger.debug(f"{request.method} {request.path} from {client_ip}")
        return await proxy_http(request)


async def _process_server_message(data, ws_client, client_ip):  # noqa: C901
    """
    Processes and filters messages coming from the Home Assistant server.
    Filters based on the specific client's Lovelace entities.
    """
    # Get the client state
    client_state = CLIENT_STATES.get(ws_client)
    if client_state is None:
        logger.info(f"No client state found for {client_ip}, passing message directly")
        return json.dumps(data if isinstance(data, list) else data)

    client_entities = client_state.lovelace_entities
    client_rules = client_state.filter_rules
    messages_to_process = data if isinstance(data, list) else [data]
    modified_messages = []

    for msg in messages_to_process:
        lovelace_config_id = client_state.lovelace_config_id
        subscribe_entities_id = client_state.subscribe_entities_id

        # Check for the lovelace/config response for this client
        if (
            "id" in msg
            and msg["id"] == lovelace_config_id
            and "type" in msg
            and msg["type"] == "result"
            and msg.get("success")
        ):
            lovelace_config = msg.get("result")
            if lovelace_config:
                # Parse entities and rules
                parse_lovelace_entities(lovelace_config, client_entities, client_rules)
                if client_rules:
                    logger.debug(f"Auto-entities rules: {client_rules}")
                    all_states = client_state.all_states
                    if all_states:
                        _resolve_rules_and_update_entities(
                            all_states, client_rules, client_entities
                        )
                        client_state.all_states = None
                logger.info(
                    f"{len(client_entities)} entities with {len(client_rules)} auto-entities rules tracked for {client_ip}"
                )
                # Once config is parsed, we no longer need to track the ID
                client_state.lovelace_config_id = None

            modified_messages.append(msg)

        # Store the intial states from subscribe_entities
        elif (
            "id" in msg
            and msg["id"] == subscribe_entities_id
            and msg.get("type") == "event"
        ):
            compressed_states = msg.get("event", {}).get("a")
            client_state.all_states = compressed_states
            # We've processed the rules, so we can remove the subscription ID
            client_state.subscribe_entities_id = None
            modified_messages.append(msg)

        # Filter 'event' messages ONLY IF the client's Lovelace entities list is populated
        elif "type" in msg and msg["type"] == "event":
            if client_entities:
                compressed_updates = msg.get("event", {}).get("c")
                if isinstance(compressed_updates, dict):
                    include_pattern = re.compile(r"^(update|event)\.")
                    # Filter updates to include only entities relevant to this client
                    filtered_updates = {
                        entity_id: diff_data
                        for entity_id, diff_data in compressed_updates.items()
                        if _entity_id_matches(entity_id, client_entities)
                        or include_pattern.match(entity_id)
                    }
                    if filtered_updates:
                        msg["event"]["c"] = filtered_updates
                        modified_messages.append(msg)
                    else:
                        # If no entities match the filter, do not add this message
                        continue
                else:
                    modified_messages.append(msg)
            else:
                # If no client_entities are parsed yet for this client, pass events through
                modified_messages.append(msg)

        # For all other messages, pass them through
        else:
            modified_messages.append(msg)

    if not modified_messages:
        # If no messages are left after filtering
        return None

    return json.dumps(
        modified_messages if isinstance(data, list) else modified_messages[0]
    )


async def proxy_websocket_filtered(request, client_ip):
    session = request.app["client_session"]
    ws_client = aiohttp.web.WebSocketResponse()
    await ws_client.prepare(request)

    headers = {
        key: value for key, value in request.headers.items() if key.lower() != "host"
    }

    # Create client state
    CLIENT_STATES[ws_client] = ClientState(client_ip=client_ip)

    try:
        async with session.ws_connect(
            f"ws://{HA_HOST}{request.path_qs}", headers=headers
        ) as ws_server:
            close_signal = asyncio.Future()
            client_to_server = asyncio.create_task(
                _client_to_server_proxy(ws_client, ws_server, client_ip, close_signal)
            )
            server_to_client = asyncio.create_task(
                _server_to_client_proxy(ws_server, ws_client, client_ip, close_signal)
            )

            logger.debug(
                f"Filtered WebSocket proxy established to {request.path_qs} from {client_ip}"
            )

            done, pending = await asyncio.wait(
                [client_to_server, server_to_client],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    except aiohttp.ClientError as e:
        logger.error(f"WebSocket connection error for {client_ip}: {e}")
    finally:
        # Clean up client state
        if ws_client in CLIENT_STATES:
            del CLIENT_STATES[ws_client]
        if not ws_client.closed:
            await ws_client.close()

    return ws_client


async def _client_to_server_proxy(ws_client, ws_server, client_ip, close_signal):
    """Handles messages from the client and forwards them to the server."""
    client_state = CLIENT_STATES.get(ws_client)

    try:
        async for message in ws_client:
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(message.data)
                    if data.get("type") == "lovelace/config" and "url_path" in data:
                        lovelace_config_id = data.get("id")
                        if client_state:
                            client_state.lovelace_config_id = lovelace_config_id
                        logger.debug(
                            f"lovelace/config request with ID: {lovelace_config_id} for {client_ip}"
                        )

                    # Intercept subscribe_entities to find the full state dump later
                    if data.get("type") == "subscribe_entities":
                        states_id = data.get("id")
                        if client_state:
                            client_state.subscribe_entities_id = states_id
                        logger.debug(
                            f"subscribe_entities request with ID: {states_id} for {client_ip}"
                        )

                    if not close_signal.done():
                        await ws_server.send_str(message.data)
                except json.JSONDecodeError:
                    if not close_signal.done():
                        await ws_server.send_str(message.data)

            elif message.type == aiohttp.WSMsgType.ERROR:
                logger.error(f"Client connection error for {client_ip}")
                break

            else:
                if not close_signal.done():
                    await ws_server.send_bytes(message.data)

    except asyncio.CancelledError:
        pass
    finally:
        if not close_signal.done():
            close_signal.set_result(True)
        if not ws_server.closed:
            await ws_server.close()


async def _server_to_client_proxy(ws_server, ws_client, client_ip, close_signal):
    """Handles messages from the server and forwards them to the client."""
    try:
        async for message in ws_server:
            if message.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(message.data)
                    modified_message = await _process_server_message(
                        data, ws_client, client_ip
                    )
                    if modified_message and not ws_client.closed:
                        await ws_client.send_str(modified_message)
                    elif ws_client.closed:
                        logger.warning(f"Client connection for {client_ip} is closed")
                        break
                except json.JSONDecodeError:
                    if not ws_client.closed:
                        await ws_client.send_str(message.data)
                    else:
                        break

            elif message.type == aiohttp.WSMsgType.ERROR:
                logger.info(f"Server connection closed for {client_ip}")
                break

            else:
                if not ws_client.closed:
                    await ws_client.send_bytes(message.data)
                else:
                    break
    except asyncio.CancelledError:
        pass
    except aiohttp.ClientConnectionError as e:
        logger.error(f"Server to client proxy error: {e}")
    finally:
        if not close_signal.done():
            close_signal.set_result(True)
        if ws_client and not ws_client.closed:
            try:
                await ws_client.close()
            except Exception:
                pass


async def proxy_websocket_transparent(request, client_ip):
    ws_client = aiohttp.web.WebSocketResponse()
    await ws_client.prepare(request)
    session = request.app["client_session"]

    headers = {
        key: value for key, value in request.headers.items() if key.lower() != "host"
    }

    try:
        ws_server = await session.ws_connect(
            f"ws://{HA_HOST}{request.path_qs}", headers=headers
        )

        async def client_to_server_raw(client_ws, server_ws):
            """Reads messages from the client and forwards them to the server."""
            async for msg in client_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await server_ws.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"Client WebSocket error for {client_ip}: {msg.data}")
                    break
                else:
                    await server_ws.send_bytes(msg.data)
            await server_ws.close()

        async def server_to_client_raw(server_ws, client_ws):
            """Reads messages from the server and forwards them to the client."""
            async for msg in server_ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await client_ws.send_str(msg.data)
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    logger.error(f"Server WebSocket error for {client_ip}: {msg.data}")
                    break
                else:
                    await client_ws.send_bytes(msg.data)
            await client_ws.close()

        await asyncio.gather(
            client_to_server_raw(ws_client, ws_server),
            server_to_client_raw(ws_server, ws_client),
        )

    except (aiohttp.ClientError, asyncio.CancelledError) as e:
        logger.error(f"Transparent WebSocket connection error for {client_ip}: {e}")
    finally:
        if not ws_client.closed:
            await ws_client.close()

    return ws_client


def _get_timeout_for_path(path: str) -> Optional[int]:
    """Determine appropriate timeout based on request path."""
    if path.endswith(".m3u8"):
        return 15
    elif path.endswith("/logs/follow"):
        return None  # No timeout for streaming logs
    return 5  # Default timeout


async def proxy_http(request):
    """
    Proxies an incoming HTTP request to an upstream server, handling headers
    and streaming the response.
    """
    client = request.app.get("http_client")
    if client is None:
        logger.error("HTTP client not initialized")
        return web.Response(status=500, text="Internal Server Error")

    current_timeout = _get_timeout_for_path(request.path)
    if current_timeout != 5:
        logger.debug(f"Timeout set to {current_timeout}s for {request.path}")

    # Pass all original request headers from the client to the upstream server, except 'host'.
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in ("host",)
    }

    if TRANSPARENT:
        headers.pop("X-Forwarded-For", None)
    else:
        requester_ip = request.remote
        x_forwarded_for = headers.get("X-Forwarded-For")

        if x_forwarded_for:
            headers["X-Forwarded-For"] = f"{x_forwarded_for}, {requester_ip}"
        else:
            headers["X-Forwarded-For"] = requester_ip

    request_body = await request.read()

    try:
        async with client.stream(
            request.method,
            f"http://{HA_HOST}{request.path_qs}",
            headers=headers,
            content=request_body,
            timeout=current_timeout,
        ) as resp:
            response_headers = dict(resp.headers)

            # Create an aiohttp response object to stream the data back.
            proxy_response = web.StreamResponse(
                status=resp.status_code,
                headers=response_headers,
            )

            await proxy_response.prepare(request)

            # Iterate over the raw bytes stream from httpx.
            async for chunk in resp.aiter_raw():
                await proxy_response.write(chunk)

            return proxy_response

    except httpx.ReadTimeout as e:
        logger.error(f"Upstream read timeout: {e}")
        return web.Response(status=504, text="Gateway Timeout")

    except httpx.RequestError as e:
        logger.error(f"HTTP proxy error: {e}")
        return web.Response(status=502, text="Proxy Error")


async def cleanup(app):
    """Graceful shutdown handler for client sessions."""
    if "client_session" in app:
        session = app["client_session"]
        if hasattr(session, "closed") and not session.closed:
            await session.close()
            logger.info("aiohttp client session closed.")

    if "http_client" in app:
        http_client = app["http_client"]
        if hasattr(http_client, "is_closed") and not http_client.is_closed:
            await http_client.aclose()
            logger.info("httpx client session closed.")


async def shutdown(app, runner, site):
    """Gracefully shuts down the web server and cancels all tasks."""
    logger.info("Starting graceful shutdown...")

    # Stop accepting new connections
    await site.stop()

    # Cancel all running tasks
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()

    # Wait for tasks to finish with a short timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), timeout=3.0
        )
    except asyncio.TimeoutError:
        logger.warning("Tasks did not shut down within the timeout. Forcing shutdown.")

    # Close the aiohttp runner and cleanup
    await runner.cleanup()
    logger.info("Server runner and tasks are cleaned up.")

    # Close the aiohttp client session
    await cleanup(app)


async def main():
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", proxy_handler)

    app["client_session"] = aiohttp.ClientSession()
    app["http_client"] = httpx.AsyncClient(verify=False, timeout=5)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PROXY_PORT)
    await site.start()

    logger.info(f"Proxy started for port {PROXY_PORT} -> {HA_HOST}")

    # Use an Event for a clean shutdown signal
    shutdown_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, shutdown_event.set)
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    try:
        # This will block until the shutdown event is set
        await shutdown_event.wait()
    except asyncio.CancelledError:
        # This can happen if the event loop is cancelled
        pass

    # Start the shutdown process
    await shutdown(app, runner, site)
    logger.info("Proxy shut down gracefully")


if __name__ == "__main__":
    logger.info("Starting Dasher proxy")
    try:
        # Check if the code is running inside the add-on container.
        if os.path.exists("/data/options.json"):
            asyncio.run(main())
        else:
            # If not in the container, use proxy-config.yaml for local testing
            logger.info("Running in dev mode with proxy-config.yaml")
            try:
                with open("proxy-config.yaml", "r") as f:
                    config_local = yaml.safe_load(f)
            except FileNotFoundError:
                logger.error(
                    "Configuration file 'proxy-config.yaml' not found, creating a default one"
                )
                default_config = {"ha_host": HA_HOST, "proxy_port": PROXY_PORT}
                with open("proxy-config.yaml", "w") as f:
                    yaml.dump(default_config, f)
                config_local = default_config

            HA_HOST = config_local["ha_host"]
            PROXY_PORT = config_local["proxy_port"]
            asyncio.run(main())

    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down")
