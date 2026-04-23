use flate2::read::{GzDecoder, ZlibDecoder};
use flate2::write::{GzEncoder, ZlibEncoder};
use flate2::Compression;
use std::io::{Read, Write};

const INJECTED_SCRIPT: &str = r#"<script>(function(){var t='';try{t=crypto.randomUUID()}catch(e){t=Math.random().toString(36).substring(2)+Date.now().toString(36)}var W=window.WebSocket;window.WebSocket=function(u,p){if(typeof u==='string'&&u.indexOf('/api/websocket')!==-1){u=u+(u.indexOf('?')!==-1?'&':'?')+'dasher_tab='+encodeURIComponent(t)}return new W(u,p)};function s(){fetch('/dasher/panel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({tab_id:t,url_path:location.pathname})}).catch(function(){})}s();var P=history.pushState;var R=history.replaceState;history.pushState=function(){P.apply(this,arguments);s()};history.replaceState=function(){R.apply(this,arguments);s()};window.addEventListener('popstate',s)})();</script>"#;

const MAX_HTML_SIZE: usize = 1024 * 1024; // 1MB

pub fn process_html_response(
    body: &[u8],
    content_encoding: Option<&str>,
) -> (Vec<u8>, Option<String>) {
    if body.len() > MAX_HTML_SIZE {
        tracing::warn!(
            "HTML response too large ({} bytes), skipping injection",
            body.len()
        );
        return (body.to_vec(), content_encoding.map(|s| s.to_string()));
    }

    let html = match content_encoding {
        Some("gzip") => decompress_gzip(body),
        Some("deflate") => decompress_deflate(body),
        _ => Ok(body.to_vec()),
    };

    let html = match html {
        Ok(h) => h,
        Err(e) => {
            tracing::warn!("Failed to decompress HTML: {}, passing through", e);
            return (body.to_vec(), content_encoding.map(|s| s.to_string()));
        }
    };

    let html_str = match String::from_utf8(html) {
        Ok(s) => s,
        Err(_) => {
            tracing::warn!("HTML is not valid UTF-8, passing through");
            return (body.to_vec(), content_encoding.map(|s| s.to_string()));
        }
    };

    let injected = inject_script(&html_str);

    match content_encoding {
        Some("gzip") => match compress_gzip(injected.as_bytes()) {
            Ok(compressed) => (compressed, Some("gzip".to_string())),
            Err(e) => {
                tracing::warn!("Failed to recompress HTML: {}, serving uncompressed", e);
                (injected.into_bytes(), None)
            }
        },
        Some("deflate") => match compress_deflate(injected.as_bytes()) {
            Ok(compressed) => (compressed, Some("deflate".to_string())),
            Err(e) => {
                tracing::warn!("Failed to recompress HTML: {}, serving uncompressed", e);
                (injected.into_bytes(), None)
            }
        },
        _ => (injected.into_bytes(), None),
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

fn decompress_gzip(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut decoder = GzDecoder::new(data);
    let mut result = Vec::new();
    decoder.read_to_end(&mut result)?;
    Ok(result)
}

fn decompress_deflate(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut decoder = ZlibDecoder::new(data);
    let mut result = Vec::new();
    decoder.read_to_end(&mut result)?;
    Ok(result)
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
    fn test_process_html_uncompressed() {
        let html = "<html><head></head><body>Hello</body></html>";
        let (result, encoding) = process_html_response(html.as_bytes(), None);
        assert!(encoding.is_none());
        let result_str = String::from_utf8(result).unwrap();
        assert!(result_str.contains("dasher_tab="));
    }

    #[test]
    fn test_process_html_deflate() {
        let html = "<html><head></head><body>Hello</body></html>";
        let compressed = compress_deflate(html.as_bytes()).unwrap();
        let (result, encoding) = process_html_response(&compressed, Some("deflate"));
        assert_eq!(encoding, Some("deflate".to_string()));
        let decompressed = decompress_deflate(&result).unwrap();
        let result_str = String::from_utf8(decompressed).unwrap();
        assert!(result_str.contains("dasher_tab="));
    }

    #[test]
    fn test_process_html_gzip() {
        let html = "<html><head></head><body>Hello</body></html>";
        let compressed = compress_gzip(html.as_bytes()).unwrap();
        let (result, encoding) = process_html_response(&compressed, Some("gzip"));
        assert_eq!(encoding, Some("gzip".to_string()));
        let decompressed = decompress_gzip(&result).unwrap();
        let result_str = String::from_utf8(decompressed).unwrap();
        assert!(result_str.contains("dasher_tab="));
    }

    #[test]
    fn test_process_html_too_large() {
        let html = vec![b'x'; MAX_HTML_SIZE + 1];
        let (result, encoding) = process_html_response(&html, None);
        assert_eq!(result, html);
        assert!(encoding.is_none());
    }
}
