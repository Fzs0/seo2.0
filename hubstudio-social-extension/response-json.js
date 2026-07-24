globalThis.parseExdivoJsonResponse = async function parseExdivoJsonResponse(
  response,
  requestUrl
) {
  const contentType = (response.headers.get("content-type") || "").toLowerCase();
  const body = await response.text();
  const isHtml = contentType.includes("text/html")
    || /^\s*(?:<!doctype\s+html|<html\b)/i.test(body);
  if (isHtml) {
    throw new Error(
      `后端地址返回了网页而不是 JSON（${requestUrl}）。请确认扩展连接的是 8000 端口的本地后端。`
    );
  }
  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    throw new Error(`后端返回的 JSON 格式无效（${requestUrl}）。`);
  }
  if (!response.ok) {
    throw new Error(payload.detail || `Backend returned HTTP ${response.status}`);
  }
  return payload;
};
