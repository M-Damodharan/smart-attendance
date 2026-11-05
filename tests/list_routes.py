import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import wsgi

app = wsgi.app
print(f"Total routes registered: {len(list(app.url_map.iter_rules()))}")
for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
    methods = [m for m in rule.methods if m not in ("HEAD", "OPTIONS")]
    print(f"{rule.rule:45} -> {rule.endpoint:30} {methods}")
