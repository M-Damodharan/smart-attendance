import os, re

template_dir = 'app/templates'
missing = []

for root, dirs, files in os.walk(template_dir):
    for f in files:
        if f.endswith('.html'):
            p = os.path.join(root, f)
            with open(p, 'r', encoding='utf-8') as fp:
                content = fp.read()
            # find all forms
            forms = re.findall(r'(<form\b[^>]*>)(.*?)(</form>)', content, re.DOTALL | re.IGNORECASE)
            for form_open, form_body, form_close in forms:
                # check if method="post" or method='post' or method=post
                if re.search(r'method=[\'"]?post[\'"]?', form_open, re.IGNORECASE):
                    if 'csrf_token' not in form_body and 'csrf' not in form_body:
                        missing.append((p, form_open))

print(f"Total forms missing CSRF token: {len(missing)}")
for path, tag in missing:
    clean_tag = " ".join(tag.split())
    print(f"File: {path}\n  Tag: {clean_tag}\n")
