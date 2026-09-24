"""python -m quota_core.usage [days]  -- update the index and print a text summary."""
import sys

from .index import update
from .pricing import PriceBook, refresh_litellm
from .report import PROVIDERS, build, fmt_money, fmt_tokens

days = None if len(sys.argv) > 1 and sys.argv[1] == "all" else int(sys.argv[1]) if len(sys.argv) > 1 else 30
refresh_litellm()
stats = update(lambda i, n: print(f"\rindexing {i}/{n} files", end="", flush=True))
print(f"\nindex: {stats['changed']} of {stats['files']} files read, +{stats['events_added']} events, "
      f"{stats['seconds']:.1f}s")
r = build(days, prices=PriceBook())
print(f"\n{fmt_money(r.cost)}  {r.sessions:,} sessions  ({r.start} .. {r.end}, API estimate)")
for p in r.providers:
    print(f"  {PROVIDERS.get(p.key, p.key):12} {p.sessions:5} sessions  {fmt_money(p.cost):>12}  "
          f"{r.share(p.cost):6.1%}  {fmt_tokens(p.tokens.total)} tokens")
t = r.tokens
print(f"\nprocessed {fmt_tokens(t.total)}  cache reads {fmt_tokens(t.cache_read)}  cache writes "
      f"{fmt_tokens(t.cache_write)}  uncached input {fmt_tokens(t.uncached)}  output {fmt_tokens(t.output)}  "
      f"cache savings {fmt_money(r.cache_savings)}")
print()
for m in r.models:
    flag = "" if m.priced else "  (no price)"
    print(f"  {m.key:28} {fmt_money(m.cost):>12} {r.share(m.cost):6.1%} {fmt_tokens(m.tokens.total):>7}{flag}")
