import ast
import operator

import yfinance as yf
from ddgs import DDGS
from langchain_core.tools import tool

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


def _eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


@tool
def calculator(expression: str) -> str:
    """Evaluate a math expression, e.g. '(12.5 + 7.5) * 2'."""
    return str(_eval(ast.parse(expression, mode="eval").body))


@tool
def ddg(query: str) -> str:
    """Search the web with DuckDuckGo and return the top results."""
    try:
        results = DDGS().text(query, max_results=5)
    except Exception:
        return "No results found. Try a different query."
    return "\n".join(f"{r['title']}: {r['body']} ({r['href']})" for r in results)


@tool
def yfinance_quote(ticker: str) -> str:
    """Get the latest stock price info for a ticker symbol, e.g. 'AAPL'."""
    info = yf.Ticker(ticker).fast_info
    return (
        f"{ticker.upper()}: last_price={info['last_price']} "
        f"previous_close={info['previous_close']} currency={info['currency']}"
    )


TOOLS = [calculator, ddg, yfinance_quote]
