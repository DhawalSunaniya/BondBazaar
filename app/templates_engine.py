from typing import Any
from fastapi.templating import Jinja2Templates

class CompatibleJinja2Templates(Jinja2Templates):
    def TemplateResponse(self, *args: Any, **kwargs: Any):
        # Support legacy Starlette signature: TemplateResponse("file.html", {"request": request, ...})
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], dict):
            name = args[0]
            context = args[1]
            request = kwargs.pop("request", context.get("request"))
            return super().TemplateResponse(request=request, name=name, context=context, **kwargs)
        if len(args) == 1 and isinstance(args[0], str) and "context" in kwargs:
            name = args[0]
            context = kwargs.pop("context")
            request = kwargs.pop("request", (context or {}).get("request"))
            return super().TemplateResponse(request=request, name=name, context=context, **kwargs)
        return super().TemplateResponse(*args, **kwargs)

templates = CompatibleJinja2Templates(directory="app/templates")

def format_inr(val, symbol=True):
    """Indian number formatting (e.g. ₹1,25,000.00)."""
    if val is None:
        return "₹0.00" if symbol else "0.00"
    try:
        val_f = float(val)
        is_neg = val_f < 0
        val_f = abs(val_f)
        s, dec = f"{val_f:.2f}".split(".")
        if len(s) <= 3:
            res = s
        else:
            last3 = s[-3:]
            rest = s[:-3]
            chunks = []
            while len(rest) > 2:
                chunks.append(rest[-2:])
                rest = rest[:-2]
            if rest:
                chunks.append(rest)
            chunks.reverse()
            res = ",".join(chunks) + "," + last3
        formatted = f"{'-' if is_neg else ''}{res}.{dec}"
        return f"₹{formatted}" if symbol else formatted
    except Exception:
        return str(val)

def format_pct(val):
    if val is None:
        return "0.00%"
    try:
        f = float(val)
        return f"{f*100:.2f}%" if abs(f) < 1.0 else f"{f:.2f}%"
    except Exception:
        return str(val)

# Register custom Jinja filters
templates.env.filters["inr"] = format_inr
templates.env.filters["pct"] = format_pct
templates.env.globals["zip"] = zip
