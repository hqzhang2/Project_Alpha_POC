import re

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/index.html', 'r') as f:
    content = f.read()

# We need to insert a Live Feed panel into the UI.
# Look for a place to put it, maybe next to the Portfolio tab or as a floating widget.
# The UI has tabs. Let's add a "Live Feed" tab or a persistent sidebar.
# I'll just append a Floating Live Feed widget to the bottom right of the body.

widget_html = """
    <!-- Live Feed Widget -->
    <div id="liveFeedWidget" style="position: fixed; bottom: 20px; right: 20px; width: 300px; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); z-index: 1000; overflow: hidden; display: flex; flex-direction: column;">
        <div style="background: var(--bg-dark); padding: 10px; border-bottom: 1px solid var(--border); font-weight: bold; display: flex; justify-content: space-between; align-items: center;">
            <span>🔴 Live Feed</span>
            <span id="feedStatus" style="font-size: 10px; color: var(--success);">Active</span>
        </div>
        <div id="feedContent" style="padding: 10px; max-height: 250px; overflow-y: auto; font-size: 12px; font-family: monospace;">
            <div style="color: var(--text-muted);">Initializing live feed...</div>
        </div>
    </div>

    <script>
        function updateLiveFeed() {
            fetch('/api/live_feed')
                .then(res => res.json())
                .then(data => {
                    if (data.error) return;
                    
                    let html = '<b>Live Prices:</b><br/>';
                    data.prices.forEach(p => {
                        let color = p.change >= 0 ? 'var(--success)' : 'var(--danger)';
                        html += `<div style="display:flex; justify-content:space-between; margin-bottom:4px;">
                            <span>${p.ticker}</span>
                            <span style="color:${color}">${p.price} (${p.change > 0 ? '+' : ''}${p.change}%)</span>
                        </div>`;
                    });
                    
                    html += '<hr style="border-color:var(--border); margin:8px 0;"/>';
                    html += '<b>System Events:</b><br/>';
                    data.events.forEach(e => {
                        html += `<div style="margin-bottom:4px; color:var(--text-muted);">[${e.time}] ${e.message}</div>`;
                    });
                    
                    document.getElementById('feedContent').innerHTML = html;
                    document.getElementById('feedStatus').innerText = 'Syncing...';
                    setTimeout(() => { document.getElementById('feedStatus').innerText = 'Active'; }, 1000);
                })
                .catch(err => console.error('Feed error:', err));
        }
        
        // Initial load and poll every 10 seconds
        setTimeout(updateLiveFeed, 2000);
        setInterval(updateLiveFeed, 10000);
    </script>
</body>
"""

content = content.replace("</body>", widget_html)

with open('/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS_QA/index.html', 'w') as f:
    f.write(content)
