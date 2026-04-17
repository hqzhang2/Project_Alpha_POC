import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'r') as f:
    content = f.read()

# Replace end_headers to add cache-control
old_headers = """    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()"""

new_headers = """    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        super().end_headers()"""

content = content.replace(old_headers, new_headers)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/server.py', 'w') as f:
    f.write(content)
