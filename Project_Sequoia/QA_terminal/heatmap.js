// Update time
setInterval(() => {
    const timeEl = document.getElementById('time');
    if (timeEl) timeEl.textContent = new Date().toLocaleTimeString();
}, 1000);

let currentView = 'treemap';
let treemapInstance = null;
let quotesData = {};

function switchView(view) {
    currentView = view;
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.view-container').forEach(c => c.classList.remove('active'));
    
    event.target.classList.add('active');
    document.getElementById(view + 'View').classList.add('active');
    
    renderData();
}

async function loadData() {
    try {
        // Default to Asset Class ETFs
        const tickersArray = ['AGG', 'TLT', 'SPY', 'IBIT', 'DBC', 'GLD', 'EEM', 'FXI', 'IJH', 'IWM', 'QQQ', 'VEA'];
        
        // Convert to comma-separated string
        const tickers = tickersArray.join(',');
        
        const res = await fetch(`/api/quotes?tickers=${tickers}`);
        quotesData = await res.json();
        
        renderData();
    } catch (e) {
        console.error('Heatmap load error:', e);
    }
}

function renderData() {
    if (currentView === 'treemap') {
        renderTreemap();
    } else {
        renderTabular();
    }
}

// Generates a hex intensity color from Red to Green
function getIntensityColorHex(value) {
    if (value > 2.0) return '#3fb950'; // Bright Green
    if (value > 0.0) return '#238636'; // Dark Green
    if (value < -2.0) return '#ff7b72'; // Bright Red
    if (value < 0.0) return '#da3633'; // Dark Red
    return '#30363d'; // Neutral Grey
}

function renderTreemap() {
    if (treemapInstance) {
        treemapInstance.destroy();
    }
    
    let chartData = [];
    
    Object.values(quotesData).filter(q => !q.error && q.price).forEach(q => {
        // 1. Calculate Size (Market Cap or pseudo-cap for ETFs via Volume * Price)
        let size = q.market_cap;
        if (!size && q.volume && q.price) size = q.volume * q.price * 10; // Scale ETF size up a bit for visibility
        if (!size) size = 1e9; // Base 1B if data completely missing
        
        let perf = q.change_pct || q.ret_1d || 0;
        
        chartData.push({
            x: `${q.ticker}\n${perf > 0 ? '+' : ''}${perf.toFixed(2)}%`,
            y: size,
            fillColor: getIntensityColorHex(perf)
        });
    });

    // Sort to make treemap layout cleaner (largest items top-left)
    chartData.sort((a, b) => b.y - a.y);

    const options = {
        series: [{
            data: chartData
        }],
        legend: { show: false },
        chart: {
            height: 600,
            type: 'treemap',
            toolbar: { show: false },
            background: 'transparent',
            animations: { enabled: false }
        },
        dataLabels: {
            enabled: true,
            style: {
                fontSize: '14px',
                fontWeight: 'bold',
                colors: ['#ffffff']
            }
        },
        plotOptions: {
            treemap: {
                enableShades: false,
                useFillColorAsBg: true
            }
        },
        stroke: {
            show: true,
            width: 2,
            colors: ['#0d1117']
        },
        tooltip: {
            theme: 'dark',
            y: {
                formatter: function(val) {
                    return val >= 1e12 ? (val/1e12).toFixed(2)+'T' : (val/1e9).toFixed(2)+'B';
                }
            }
        }
    };

    treemapInstance = new ApexCharts(document.querySelector("#treemapChart"), options);
    treemapInstance.render();
}

function renderTabular() {
    const container = document.getElementById('tabularContainer');
    
    let html = `<table class="tabular-table">
        <thead>
            <tr>
                <th style="text-align:left">Ticker</th>
                <th>Price</th>
                <th>1D</th>
                <th>1W</th>
                <th>1M</th>
                <th>3M</th>
                <th>YTD</th>
                <th>1Y</th>
            </tr>
        </thead>
        <tbody>
    `;
    
    // Sort alphabetically
    const sortedQuotes = Object.values(quotesData)
        .filter(q => !q.error)
        .sort((a, b) => a.ticker.localeCompare(b.ticker));
    
    sortedQuotes.forEach(q => {
        // The properties mapping from our Quotes API
        const periods = [
            { key: 'change_pct', fallback: 'ret_1d' },
            { key: 'ret_1w' },
            { key: 'ret_1m' },
            { key: 'ret_3m' },
            { key: 'ret_ytd' },
            { key: 'ret_1y' }
        ];
        
        html += `<tr>
            <td class="ticker">${q.ticker}</td>
            <td class="price">$${q.price?.toFixed(2) || '-'}</td>
        `;
        
        periods.forEach(p => {
            const val = q[p.key] ?? q[p.fallback] ?? 0;
            const color = getIntensityColorHex(val);
            const sign = val > 0 ? '+' : '';
            html += `<td class="perf" style="background-color: ${color};">
                ${sign}${val.toFixed(2)}%
            </td>`;
        });
        
        html += `</tr>`;
    });
    
    html += `</tbody></table>`;
    container.innerHTML = html;
}

// Init immediately and set auto-refresh
loadData();
setInterval(loadData, 60000);