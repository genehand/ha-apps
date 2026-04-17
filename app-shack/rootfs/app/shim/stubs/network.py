"""Home Assistant network utilities stub module.

Provides homeassistant.util.network with IP address utilities.
"""

import sys
import types
from ipaddress import IPv4Address, IPv6Address, ip_address, ip_network
import re


def create_network_stubs(hass, homeassistant):
    """Create homeassistant.util.network stub module."""

    network = types.ModuleType("homeassistant.util.network")

    # RFC6890 - IP addresses of loopback interfaces
    network.IPV6_IPV4_LOOPBACK = ip_network("::ffff:127.0.0.0/104")

    network.LOOPBACK_NETWORKS = (
        ip_network("127.0.0.0/8"),
        ip_network("::1/128"),
        network.IPV6_IPV4_LOOPBACK,
    )

    # RFC6890 - Address allocation for Private Internets
    network.PRIVATE_NETWORKS = (
        ip_network("10.0.0.0/8"),
        ip_network("172.16.0.0/12"),
        ip_network("192.168.0.0/16"),
        ip_network("fd00::/8"),
        ip_network("::ffff:10.0.0.0/104"),
        ip_network("::ffff:172.16.0.0/108"),
        ip_network("::ffff:192.168.0.0/112"),
    )

    # RFC6890 - Link local ranges
    network.LINK_LOCAL_NETWORKS = (
        ip_network("169.254.0.0/16"),
        ip_network("fe80::/10"),
        ip_network("::ffff:169.254.0.0/112"),
    )

    def is_loopback(address: IPv4Address | IPv6Address) -> bool:
        """Check if an address is a loopback address."""
        return address.is_loopback or address in network.IPV6_IPV4_LOOPBACK

    def is_private(address: IPv4Address | IPv6Address) -> bool:
        """Check if an address is a unique local non-loopback address."""
        return any(address in net for net in network.PRIVATE_NETWORKS)

    def is_link_local(address: IPv4Address | IPv6Address) -> bool:
        """Check if an address is link-local (local but not necessarily unique)."""
        return address.is_link_local

    def is_local(address: IPv4Address | IPv6Address) -> bool:
        """Check if an address is on a local network."""
        return is_loopback(address) or is_private(address) or is_link_local(address)

    def is_invalid(address: IPv4Address | IPv6Address) -> bool:
        """Check if an address is invalid."""
        return address.is_unspecified

    def is_ip_address(address: str) -> bool:
        """Check if a given string is an IP address."""
        try:
            ip_address(address)
        except ValueError:
            return False
        return True

    def is_ipv4_address(address: str) -> bool:
        """Check if a given string is an IPv4 address."""
        try:
            IPv4Address(address)
        except ValueError:
            return False
        return True

    def is_ipv6_address(address: str) -> bool:
        """Check if a given string is an IPv6 address."""
        try:
            IPv6Address(address)
        except ValueError:
            return False
        return True

    def is_host_valid(host: str) -> bool:
        """Check if a given string is an IP address or valid hostname."""
        if is_ip_address(host):
            return True
        if len(host) > 255:
            return False
        if re.match(r"^[0-9\.]+", host):  # reject invalid IPv4
            return False
        host = host.removesuffix(".")
        allowed = re.compile(r"(?!-)[A-Z\d\-]{1,63}(?<!-)$", re.IGNORECASE)
        return all(allowed.match(x) for x in host.split("."))

    network.is_loopback = is_loopback
    network.is_private = is_private
    network.is_link_local = is_link_local
    network.is_local = is_local
    network.is_invalid = is_invalid
    network.is_ip_address = is_ip_address
    network.is_ipv4_address = is_ipv4_address
    network.is_ipv6_address = is_ipv6_address
    network.is_host_valid = is_host_valid

    # Register in both sys.modules and on homeassistant.util
    sys.modules["homeassistant.util.network"] = network
    if hasattr(homeassistant, "util") and homeassistant.util is not None:
        homeassistant.util.network = network

    return homeassistant
