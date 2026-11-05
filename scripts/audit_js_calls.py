import os, re

search_dirs = ['app/static/js', 'app/templates']
api_calls = []

for sdir in search_dirs:
    for root, dirs, files in os.walk(sdir):
        for f in files:
            if f.endswith('.js') or f.endswith('.html'):
                p = os.path.join(root, f)
                with open(p, 'r', encoding='utf-8') as fp:
                    content = fp.read()
                # find fetch calls
                matches = re.finditer(r'fetch\s*\(\s*([^\)]+)\)', content)
                for m in matches:
                    snippet = m.group(0)[:100].replace('\n', ' ')
                    api_calls.append((p, snippet))

print(f"Total fetch calls found: {len(api_calls)}")
for p, s in api_calls[:15]:
    print(f"{p}: {s}")
