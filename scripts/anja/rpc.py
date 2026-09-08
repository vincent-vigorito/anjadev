"""Validazione dell'envelope condivisa dai server MCP stdio."""
import math
from functools import wraps


def validated_request(handler):
    @wraps(handler)
    def dispatch(req):
        def error(code, message, req_id=None):
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

        if not isinstance(req, dict):
            return error(-32600, "request must be an object (batch requests are not supported)")
        req_id = req.get("id")
        if (req.get("jsonrpc") != "2.0" or not isinstance(req.get("method"), str)
                or isinstance(req_id, bool) or not isinstance(req_id, (str, int, float, type(None)))
                or (isinstance(req_id, float) and not math.isfinite(req_id))):
            return error(-32600, "invalid JSON-RPC request")
        notification = "id" not in req
        params = req.get("params", {})
        problem = None
        if not isinstance(params, dict):
            problem = "params must be an object"
        elif req["method"] == "tools/call":
            if not isinstance(params.get("name"), str) or not params["name"]:
                problem = "tool name must be a non-empty string"
            elif not isinstance(params.get("arguments", {}), dict):
                problem = "arguments must be an object"
        if problem:
            return None if notification else error(-32602, problem, req_id)
        response = handler(req)
        return None if notification else response

    return dispatch
