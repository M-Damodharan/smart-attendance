import base64

with open('app/static/img/icon-512.png', 'rb') as f:
    b64 = base64.b64encode(f.read()).decode('utf-8')

svg_content = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 512 512" width="512" height="512">
  <image width="512" height="512" href="data:image/png;base64,{b64}"/>
</svg>'''

with open('app/static/img/icon.svg', 'w', encoding='utf-8') as f:
    f.write(svg_content)

print('Updated app/static/img/icon.svg successfully!')
