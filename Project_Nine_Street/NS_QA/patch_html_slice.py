import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/index.html', 'r') as f:
    content = f.read()

# Remove the slice(0,8) limit
content = content.replace("data.slice(0, 8).forEach(r => {", "data.forEach(r => {")

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/index.html', 'w') as f:
    f.write(content)
