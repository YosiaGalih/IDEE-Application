import { createServer as createHttpServer } from "node:http";

function sendJson(response, status, body) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(body));
}

export function createServer() {
  return createHttpServer((request, response) => {
    if (request.method === "GET" && request.url === "/health") {
      sendJson(response, 200, { status: "ok" });
      return;
    }
    sendJson(response, 404, { error: "not found" });
  });
}
