use brotli::Decompressor as BrotliDecompressor;
use flate2::read::{DeflateDecoder, GzDecoder, ZlibDecoder};
use flate2::write::{GzEncoder, ZlibEncoder};
use flate2::Compression;
use std::io::{Read, Write};

const INJECTED_SCRIPT: &str = r#"<script>(function(){var t='';try{t=crypto.randomUUID()}catch(e){t=Math.random().toString(36).substring(2)+Date.now().toString(36)}var W=window.WebSocket;window.WebSocket=function(u,p){if(typeof u==='string'&&u.indexOf('/api/websocket')!==-1){u=u+(u.indexOf('?')!==-1?'&':'?')+'dasher_tab='+encodeURIComponent(t)}return new W(u,p)};function s(){fetch('/dasher/panel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tab_id:t,url_path:location.pathname})}).catch(function(){})}s();var P=history.pushState;var R=history.replaceState;history.pushState=function(){P.apply(this,arguments);s()};history.replaceState=function(){R.apply(this,arguments);s()};window.addEventListener('popstate',s);document.addEventListener('visibilitychange',function(){if(!document.hidden)s()})})();</script>"#;

const MAX_HTML_SIZE: usize = 1024 * 1024; // 1MB
const MAX_DECOMPRESSED_HTML: usize = 16 * 1024 * 1024; // 16MB

pub fn process_html_response(
    body: &[u8],
    content_encoding: Option<&str>,
    inject: bool,
) -> (Vec<u8>, Option<String>) {
    if body.len() > MAX_HTML_SIZE {
        tracing::warn!(
            "HTML response too large ({} bytes), skipping injection",
            body.len()
        );
        return (body.to_vec(), content_encoding.map(|s| s.to_string()));
    }

    let html = match content_encoding {
        Some(enc) => match decompress_body(body, enc) {
            Ok(h) => h,
            Err(e) => {
                tracing::warn!(
                    "Cannot process HTML with content-encoding {}: {}, passing through",
                    enc,
                    e
                );
                return (body.to_vec(), content_encoding.map(|s| s.to_string()));
            }
        },
        None => body.to_vec(),
    };

    let html_str = match String::from_utf8(html) {
        Ok(s) => s,
        Err(_) => {
            tracing::warn!(
                "HTML is not valid UTF-8 (content-encoding: {:?}), passing through",
                content_encoding
            );
            return (body.to_vec(), content_encoding.map(|s| s.to_string()));
        }
    };

    let processed = if inject {
        inject_script(&html_str)
    } else {
        html_str
    };

    // Recompress with gzip (or keep deflate) if the upstream body was
    // compressed. Encodings we decode but don't re-emit (br/zstd) fall back
    // to gzip, which every browser supports. Uncompressed bodies pass through
    // as-is.
    match content_encoding {
        Some(enc) if is_supported_encoding(enc) => {
            let encoding = if enc.trim().eq_ignore_ascii_case("deflate") {
                "deflate"
            } else {
                "gzip"
            };
            let compressed = if encoding == "deflate" {
                compress_deflate(processed.as_bytes())
            } else {
                compress_gzip(processed.as_bytes())
            };
            match compressed {
                Ok(compressed) => (compressed, Some(encoding.to_string())),
                Err(e) => {
                    tracing::warn!("Failed to recompress HTML: {}, serving uncompressed", e);
                    (processed.into_bytes(), None)
                }
            }
        }
        _ => (processed.into_bytes(), None),
    }
}

fn inject_script(html: &str) -> String {
    // Skip if already injected
    if html.contains("dasher_tab=") {
        return html.to_string();
    }

    let lower = html.to_lowercase();

    // Try to find <head> tag and inject after its opening
    if let Some(head_pos) = lower.find("<head>") {
        let insert_pos = head_pos + "<head>".len();
        return inject_at_pos(html, insert_pos);
    }

    if let Some(head_pos) = lower.find("<head ") {
        if let Some(close_pos) = html[head_pos..].find('>') {
            let insert_pos = head_pos + close_pos + 1;
            return inject_at_pos(html, insert_pos);
        }
    }

    // Fallback: prepend after <html>
    if let Some(html_pos) = lower.find("<html>") {
        let insert_pos = html_pos + "<html>".len();
        return inject_at_pos(html, insert_pos);
    }

    if let Some(html_pos) = lower.find("<html ") {
        if let Some(close_pos) = html[html_pos..].find('>') {
            let insert_pos = html_pos + close_pos + 1;
            return inject_at_pos(html, insert_pos);
        }
    }

    // Last resort: return as-is
    html.to_string()
}

fn inject_at_pos(html: &str, pos: usize) -> String {
    let mut result = String::with_capacity(html.len() + INJECTED_SCRIPT.len());
    result.push_str(&html[..pos]);
    result.push_str(INJECTED_SCRIPT);
    result.push_str(&html[pos..]);
    result
}

/// Decompress an HTML body based on its `Content-Encoding` header value.
/// Header values are case-insensitive and may carry whitespace.
fn decompress_body(data: &[u8], content_encoding: &str) -> Result<Vec<u8>, String> {
    let enc = content_encoding.trim().to_ascii_lowercase();
    match enc.as_str() {
        "gzip" | "x-gzip" => decompress_gzip(data).map_err(|e| format!("gzip: {}", e)),
        // RFC 2616 says "deflate" is zlib-wrapped, but some servers (and
        // proxies) send raw deflate; try both.
        "deflate" => decompress_deflate(data)
            .or_else(|_| decompress_raw_deflate(data))
            .map_err(|e| format!("deflate: {}", e)),
        "br" => decompress_brotli(data).map_err(|e| format!("brotli: {}", e)),
        "zstd" => decompress_zstd(data).map_err(|e| format!("zstd: {}", e)),
        "identity" => Ok(data.to_vec()),
        other => Err(format!("unsupported encoding {:?}", other)),
    }
}

fn is_supported_encoding(content_encoding: &str) -> bool {
    matches!(
        content_encoding.trim().to_ascii_lowercase().as_str(),
        "gzip" | "x-gzip" | "deflate" | "br" | "zstd"
    )
}

/// Read to end but bound the output size so a malicious or misconfigured
/// upstream cannot balloon memory via a zip bomb.
fn read_limited<R: Read>(reader: R) -> Result<Vec<u8>, std::io::Error> {
    let mut reader = reader.take((MAX_DECOMPRESSED_HTML + 1) as u64);
    let mut result = Vec::new();
    reader.read_to_end(&mut result)?;
    if result.len() > MAX_DECOMPRESSED_HTML {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "decompressed HTML exceeds limit",
        ));
    }
    Ok(result)
}

fn decompress_brotli(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let decoder = BrotliDecompressor::new(data, 8192);
    read_limited(decoder)
}

fn decompress_zstd(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    use ruzstd::decoding::StreamingDecoder;

    let decoder = StreamingDecoder::new(data)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    read_limited(decoder)
}

/// Raw (headerless) deflate, used by servers that ignore RFC 2616.
fn decompress_raw_deflate(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let decoder = DeflateDecoder::new(data);
    read_limited(decoder)
}

fn decompress_gzip(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let decoder = GzDecoder::new(data);
    read_limited(decoder)
}

fn decompress_deflate(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let decoder = ZlibDecoder::new(data);
    read_limited(decoder)
}

fn compress_gzip(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data)?;
    encoder.finish()
}

fn compress_deflate(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut encoder = ZlibEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(data)?;
    encoder.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_inject_script_simple_head() {
        let html = "<html><head></head><body></body></html>";
        let result = inject_script(html);
        assert!(result.contains("dasher_tab="));
        assert!(result.contains("</head>"));
        // Should be after <head>
        let head_end = result.find("<head>").unwrap() + "<head>".len();
        assert!(result[head_end..].starts_with("<script>"));
    }

    #[test]
    fn test_inject_script_head_with_attributes() {
        let html = r#"<html><head lang="en"></head><body></body></html>"#;
        let result = inject_script(html);
        assert!(result.contains("dasher_tab="));
        let head_end = result.find("<head lang=\"en\">").unwrap() + r#"<head lang="en">"#.len();
        assert!(result[head_end..].starts_with("<script>"));
    }

    #[test]
    fn test_inject_script_no_head() {
        let html = "<html><body></body></html>";
        let result = inject_script(html);
        assert!(result.contains("dasher_tab="));
        let html_end = result.find("<html>").unwrap() + "<html>".len();
        assert!(result[html_end..].starts_with("<script>"));
    }

    #[test]
    fn test_inject_script_skips_already_injected() {
        let html = "<html><head><script>dasher_tab=abc</script></head><body></body></html>";
        let result = inject_script(html);
        // Should only contain one instance of the script
        let count = result.matches("<script>").count();
        assert_eq!(count, 1);
    }

    #[test]
    fn test_injected_script_reports_panel_on_visibility_resume() {
        let html = "<html><head></head><body></body></html>";
        let result = inject_script(html);
        // The script must re-report the panel when the tab becomes visible
        // again, since a reconnect after the 5-minute background-suspend does
        // not trigger a navigation event.
        assert!(result.contains("visibilitychange"));
        assert!(result.contains("!document.hidden"));
        // The re-report must reuse the existing panel POST helper
        assert!(result.contains("function(){if(!document.hidden)s()}"));
    }

    #[test]
    fn test_process_html_uncompressed_with_inject() {
        let html = "<html><head></head><body>Hello</body></html>";
        let (result, encoding) = process_html_response(html.as_bytes(), None, true);
        assert!(encoding.is_none());
        let result_str = String::from_utf8(result).unwrap();
        assert!(result_str.contains("dasher_tab="));
    }

    #[test]
    fn test_process_html_uncompressed_without_inject() {
        let html = "<html><head></head><body>Hello</body></html>";
        let (result, encoding) = process_html_response(html.as_bytes(), None, false);
        assert!(encoding.is_none());
        let result_str = String::from_utf8(result).unwrap();
        assert_eq!(result_str, html);
    }

    #[test]
    fn test_process_html_deflate() {
        let html = "<html><head></head><body>Hello</body></html>";
        let compressed = compress_deflate(html.as_bytes()).unwrap();
        let (result, encoding) = process_html_response(&compressed, Some("deflate"), true);
        assert_eq!(encoding, Some("deflate".to_string()));
        let decompressed = decompress_deflate(&result).unwrap();
        let result_str = String::from_utf8(decompressed).unwrap();
        assert!(result_str.contains("dasher_tab="));
    }

    #[test]
    fn test_process_html_gzip() {
        let html = "<html><head></head><body>Hello</body></html>";
        let compressed = compress_gzip(html.as_bytes()).unwrap();
        let (result, encoding) = process_html_response(&compressed, Some("gzip"), true);
        assert_eq!(encoding, Some("gzip".to_string()));
        let decompressed = decompress_gzip(&result).unwrap();
        let result_str = String::from_utf8(decompressed).unwrap();
        assert!(result_str.contains("dasher_tab="));
    }

    #[test]
    fn test_process_html_too_large() {
        let html = vec![b'x'; MAX_HTML_SIZE + 1];
        let (result, encoding) = process_html_response(&html, None, true);
        assert_eq!(result, html);
        assert!(encoding.is_none());
    }

    #[test]
    fn test_decompress_body_gzip_case_insensitive() {
        let html = b"<html><head></head><body>Hello</body></html>";
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(html).unwrap();
        let compressed = encoder.finish().unwrap();
        assert_eq!(decompress_body(&compressed, "gzip").unwrap(), html);
        assert_eq!(decompress_body(&compressed, "GZip").unwrap(), html);
        assert_eq!(decompress_body(&compressed, "x-gzip").unwrap(), html);
    }

    #[test]
    fn test_decompress_body_deflate_zlib_and_raw() {
        let html = b"<html><head></head><body>Hello</body></html>";
        // zlib-wrapped deflate (RFC 2616, what aiohttp sends)
        let mut zlib_enc = ZlibEncoder::new(Vec::new(), Compression::default());
        zlib_enc.write_all(html).unwrap();
        let zlib_data = zlib_enc.finish().unwrap();
        assert_eq!(decompress_body(&zlib_data, "deflate").unwrap(), html);
        // raw deflate (some servers and proxies ignore the RFC)
        let mut raw_enc = flate2::write::DeflateEncoder::new(Vec::new(), Compression::default());
        raw_enc.write_all(html).unwrap();
        let raw_data = raw_enc.finish().unwrap();
        assert_eq!(decompress_body(&raw_data, "deflate").unwrap(), html);
    }

    #[test]
    fn test_decompress_body_brotli() {
        let html = b"<html><head></head><body>Hello Brotli</body></html>";
        let mut encoder = brotli::CompressorWriter::new(Vec::new(), 4096, 5, 22);
        encoder.write_all(html).unwrap();
        let compressed = encoder.into_inner();
        assert_eq!(decompress_body(&compressed, "br").unwrap(), html);
        assert_eq!(decompress_body(&compressed, "BR").unwrap(), html);
    }

    /// Minimal hex decoder for embedded test vectors.
    fn hex_to_bytes(hex: &str) -> Vec<u8> {
        (0..hex.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
            .collect()
    }

    #[test]
    fn test_decompress_body_zstd() {
        // Compressed with zstd CLI v1.5.7 at level 3 from repetitive HTML so
        // the frame exercises sequence decoding, not just raw literals.
        let compressed = hex_to_bytes(
            "28b52ffd0458c5030012451015a0a56d88e26938e80081bc3ee2c392b82e79f81f81a764b3ac8cee\
             fbbeeffbbeef93879a22a0a28a802b8c32282bdb83013298470c87ba84b51b02d34ec9a6051600\
             94844000e7018e019c02b8033819e004e03cc031805300770027039c009c073806700ae00ee064\
             801386190351a3c186490603ad8175",
        );
        let expected = format!(
            "<html><head><title>Dasher Test</title></head><body>{}</body></html>",
            "Hello World, Hello World, Hello World, Hello World, Hello World. ".repeat(10)
        );
        assert_eq!(
            decompress_body(&compressed, "zstd").unwrap(),
            expected.as_bytes()
        );
        assert_eq!(
            decompress_body(&compressed, "ZSTD").unwrap(),
            expected.as_bytes()
        );
    }

    #[test]
    fn test_process_html_zstd_roundtrip() {
        let compressed = hex_to_bytes(
            "28b52ffd0458c5030012451015a0a56d88e26938e80081bc3ee2c392b82e79f81f81a764b3ac8cee\
             fbbeeffbbeef93879a22a0a28a802b8c32282bdb83013298470c87ba84b51b02d34ec9a6051600\
             94844000e7018e019c02b8033819e004e03cc031805300770027039c009c073806700ae00ee064\
             801386190351a3c186490603ad8175",
        );
        let (result, encoding) = process_html_response(&compressed, Some("zstd"), true);
        // Recompressed as gzip (encoding we can re-emit)
        assert_eq!(encoding.as_deref(), Some("gzip"));
        let decompressed = decompress_gzip(&result).unwrap();
        let html = String::from_utf8(decompressed).unwrap();
        assert!(html.contains("dasher_tab="));
        assert!(html.contains("Hello World"));
    }

    #[test]
    fn test_decompress_body_identity_and_unsupported() {
        let html = b"<html>identity</html>";
        assert_eq!(decompress_body(html, "identity").unwrap(), html);
        assert!(decompress_body(html, "compress").is_err());
        assert!(decompress_body(html, "gzip, br").is_err());
    }

    #[test]
    fn test_process_html_unsupported_encoding_passes_through() {
        let html = b"<html><head></head><body>Hello</body></html>";
        let (result, encoding) = process_html_response(html, Some("compress"), true);
        // Unsupported encoding: passed through unchanged, no injection
        assert_eq!(result, html);
        assert_eq!(encoding.as_deref(), Some("compress"));
    }
}
