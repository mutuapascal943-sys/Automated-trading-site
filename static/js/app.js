(function(){
  'use strict';

  let trialSeconds = 23*3600 + 47*60 + 12;
  let botRunning = false;
  let botInterval = null;
  let artAnimId = null;
  let pairs = {};
  let changes = {};
  let histData = [];

  var wsConnections = {};
  var botChartInstance = null;
  var botCandleSeries = null;
  var candleBuffer = {};
  var currentChartPair = null;

  var botChartContainer = null;

  var loadingSteps = [
    'Connecting Broker...',
    'Loading Market Data...',
    'Initializing AI Engine...',
    'Analyzing Markets...',
    'Preparing Dashboard...'
  ];

  const scanLines = [
    '> Scanning EUR/USD M15...',
    '> Pattern recognition: Bullish engulfing detected',
    '> EMA 50/200 crossover: Confirmed',
    '> RSI(14): 58.4 — Neutral-Bullish',
    '> MACD: Positive divergence',
    '> Volume analysis: Above average',
    '> Signal generated: BUY',
    '> Confidence: 74% — STRONG signal',
    '> TP1: Set | TP2: Set | TP3: Set',
    '> Awaiting user authorization to execute...'
  ];

  const panelTitles = {
    dashboard:'Dashboard', markets:'Markets', bot:'Trading Bot',
    analytics:'Analytics', history:'History', subscription:'Subscription', settings:'Settings'
  };

  function initData(){
    pairs = {
      'EUR/USD':1.08432,'GBP/USD':1.27380,'USD/JPY':149.820,
      'AUD/USD':0.65120,'USD/CAD':1.35840,'NZD/USD':0.60330,
      'XAU/USD':2318.50,'BTC/USD':62450.0,
      'Boom 1000 Index':1423.80,'Crash 1000 Index':987.40,
      'Volatility 75 Index':8742.10,'Volatility 100 Index':5320.60
    };
    changes = {'EUR/USD':0.12,'GBP/USD':-0.08,'USD/JPY':0.31,'AUD/USD':-0.15,
      'USD/CAD':0.07,'NZD/USD':-0.22,'XAU/USD':0.54,'BTC/USD':1.83,
      'Boom 1000 Index':0.92,'Crash 1000 Index':-0.43,
      'Volatility 75 Index':1.12,'Volatility 100 Index':0.67};
    histData = [
      ['2025-06-14','EUR/USD','BUY','1.08350','1.08690','+$84','Win','Exness'],
      ['2025-06-13','XAU/USD','SELL','2314.20','2298.50','+$156','Win','Deriv'],
      ['2025-06-13','GBP/USD','BUY','1.27180','1.26940','-$48','Loss','XM'],
      ['2025-06-12','USD/JPY','SELL','149.620','148.980','+$64','Win','Exness'],
      ['2025-06-12','BTC/USD','BUY','61240','63100','+$186','Win','Deriv'],
      ['2025-06-11','AUD/USD','BUY','0.65020','0.64880','-$28','Loss','XM'],
      ['2025-06-11','EUR/USD','SELL','1.08920','1.08540','+$76','Win','Exness'],
      ['2025-06-10','NZD/USD','BUY','0.60180','—','—','Pending','Deriv']
    ];
  }

  /* ── WebSocket Market Stream ── */
  function getMarketWS(symbol) {
    var normalised = symbol.replace(/\//g, '-');
    if (wsConnections[normalised] && wsConnections[normalised].readyState === WebSocket.OPEN) {
      return wsConnections[normalised];
    }
    var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var url = protocol + '//' + window.location.host + '/ws/market/' + normalised + '/';
    var ws = new WebSocket(url);
    ws._symbol = symbol;
    ws._normalised = normalised;
    ws._listeners = [];
    ws.onmessage = function(e) {
      var data;
      try { data = JSON.parse(e.data); } catch(_) { return; }
      if (data.type === 'tick') {
        pairs[data.symbol] = data.price;
        ws._listeners.forEach(function(cb) { cb(data); });
      }
    };
    ws.onclose = function() {
      var ns = this._normalised;
      if (wsConnections[ns] === this) delete wsConnections[ns];
      setTimeout(function() { getMarketWS(symbol); }, 3000);
    };
    wsConnections[normalised] = ws;
    return ws;
  }

  function addWSListener(symbol, cb) {
    var ws = getMarketWS(symbol);
    ws._listeners.push(cb);
  }

  function removeWSListener(symbol, cb) {
    var normalised = symbol.replace(/\//g, '-');
    var ws = wsConnections[normalised];
    if (!ws) return;
    var idx = ws._listeners.indexOf(cb);
    if (idx !== -1) ws._listeners.splice(idx, 1);
  }

  /* ── NAVIGATION ── */
  function navigateToPanel(panelId){
    document.querySelectorAll('.section-panel').forEach(function(p){p.classList.remove('active')});
    var panel = document.getElementById('panel-' + panelId);
    if(panel){panel.classList.add('active');
      panel.classList.remove('fade-in');
      void panel.offsetWidth;
      panel.classList.add('fade-in');
    }
    document.querySelectorAll('.nav-item').forEach(function(n){n.classList.remove('active')});
    var navItem = document.querySelector('.nav-item[data-panel="' + panelId + '"]');
    if(navItem){navItem.classList.add('active')}
    var titleEl = document.getElementById('topbar-title');
    if(titleEl){titleEl.textContent = panelTitles[panelId] || panelId}
    if(panelId === 'bot'){setTimeout(initBotChart,100)}
    if(panelId === 'analytics'){setTimeout(initAnalyticsCharts,100)}
    if(panelId === 'markets'){setTimeout(populateMarkets,50)}
    if(panelId === 'dashboard' && !artAnimId){initArtCanvas()}
    if(document.getElementById('sidebar') && window.innerWidth < 768){
      document.getElementById('sidebar').classList.remove('open');
    }
    window.location.hash = panelId;
  }

  window.navigateToPanel = navigateToPanel;

  function toggleSidebar(){
    var sb = document.getElementById('sidebar');
    if(sb){sb.classList.toggle('open')}
  }
  window.toggleSidebar = toggleSidebar;

  function handleHashChange(){
    var hash = window.location.hash.replace('#','');
    if(hash && panelTitles[hash]){
      navigateToPanel(hash);
    }
  }

  /* ── MARKETS ── */
  function populateMarkets(){
    var grid = document.getElementById('markets-grid');
    if(!grid || grid.children.length > 0) return;
    grid.innerHTML = Object.keys(pairs).map(function(k){
      var p = pairs[k] > 100 ? pairs[k].toFixed(2) : pairs[k].toFixed(5);
      var cls = (changes[k] || 0) >= 0 ? 'up' : 'down';
      var sign = (changes[k] || 0) >= 0 ? '+' : '';
      return '<div class="market-card" onclick="navigateToBot(\'' + k + '\')">' +
        '<div class="market-pair">' + k + '</div>' +
        '<div class="market-price">' + p + '</div>' +
        '<div class="market-change ' + cls + '">' + sign + (changes[k]||0) + '%</div></div>';
    }).join('');
  }

  function navigateToBot(pair){
    var sel = document.getElementById('bot-market');
    if(sel){sel.value = pair; updateBotMarket()}
    navigateToPanel('bot');
  }
  window.navigateToBot = navigateToBot;

  function updateMarketCards(){
    Object.keys(pairs).forEach(function(k){
      var cards = document.querySelectorAll('.market-card');
      for(var i=0;i<cards.length;i++){
        var pairEl = cards[i].querySelector('.market-pair');
        if(pairEl && pairEl.textContent === k){
          var priceEl = cards[i].querySelector('.market-price');
          if(priceEl){priceEl.textContent = pairs[k] > 100 ? pairs[k].toFixed(2) : pairs[k].toFixed(5)}
        }
      }
    });
  }

  function updateBotMarket(){
    var sel = document.getElementById('bot-market');
    var pair = sel ? sel.value : 'EUR/USD';
    var labelEl = document.getElementById('bot-pair-label');
    if(labelEl){labelEl.textContent = pair}
    var p = pairs[pair] || 1.0;
    var priceEl = document.getElementById('bot-price');
    if(priceEl){priceEl.textContent = p > 100 ? p.toFixed(2) : p.toFixed(5)}
    updateSignals(pair, p);
    initBotChart();
  }
  window.updateBotMarket = updateBotMarket;

  function updateSignals(pair, price){
    var spread = price * 0.002;
    function setText(id, val){
      var el = document.getElementById(id);
      if(el){el.textContent = val > 100 ? val.toFixed(2) : val.toFixed(5)}
    }
    setText('sig-entry', price);
    setText('sig-sl', price - spread * 1.2);
    setText('sig-tp1', price + spread);
    setText('sig-tp2', price + spread * 2);
    setText('sig-tp3', price + spread * 3.5);
    setText('sup1', price - spread * 0.8);
    setText('sup2', price - spread * 1.6);
    setText('res1', price + spread * 0.9);
    setText('res2', price + spread * 1.8);
  }

  /* ── PRICE TICKER (WebSocket driven) ── */
  function startPriceTicker(){
    Object.keys(pairs).forEach(function(k){
      getMarketWS(k);
      addWSListener(k, function(tick){
        var p = tick.price;
        pairs[k] = p;
        var sel = document.getElementById('bot-market');
        if(sel && sel.value === k){
          var priceEl = document.getElementById('bot-price');
          if(priceEl){priceEl.textContent = p > 100 ? p.toFixed(2) : p.toFixed(5)}
        }
        updateMarketCards();
      });
    });
  }

  /* ── TRIAL TIMER ── */
  function startTrialTimer(){
    setInterval(function(){
      if(trialSeconds > 0) trialSeconds--;
      var h = Math.floor(trialSeconds / 3600);
      var m = Math.floor((trialSeconds % 3600) / 60);
      var s = trialSeconds % 60;
      var el = document.getElementById('trial-timer');
      if(el){
        el.textContent = String(h).padStart(2,'0') + ':' +
          String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0');
      }
    }, 1000);
  }

  /* ── HISTORY ── */
  function populateHistory(){
    var tbody = document.getElementById('historyBody');
    if(!tbody) return;
    tbody.innerHTML = histData.map(function(r){
      var color = r[2] === 'BUY' ? 'var(--teal)' : 'var(--red)';
      var pnlColor = r[5].startsWith('+') ? 'var(--teal)' : (r[5] === '-' || r[5] === '—') ? 'var(--text2)' : 'var(--red)';
      var badgeClass = r[6].toLowerCase();
      return '<tr data-status="' + r[6] + '">' +
        '<td class="mono" style="font-size:12px">' + r[0] + '</td>' +
        '<td><strong>' + r[1] + '</strong></td>' +
        '<td class="mono" style="color:' + color + '">' + r[2] + '</td>' +
        '<td class="mono">' + r[3] + '</td>' +
        '<td class="mono">' + r[4] + '</td>' +
        '<td class="mono" style="color:' + pnlColor + '">' + r[5] + '</td>' +
        '<td><span class="badge ' + badgeClass + '">' + r[6] + '</span></td>' +
        '<td style="color:var(--text2);font-size:12px">' + r[7] + '</td></tr>';
    }).join('');
  }

  function filterTable(q){
    document.querySelectorAll('#historyBody tr').forEach(function(tr){
      tr.style.display = tr.textContent.toLowerCase().includes(q.toLowerCase()) ? '' : 'none';
    });
  }
  window.filterTable = filterTable;

  function chipFilter(el, status){
    document.querySelectorAll('.filter-chip').forEach(function(c){c.classList.remove('active')});
    el.classList.add('active');
    document.querySelectorAll('#historyBody tr').forEach(function(tr){
      tr.style.display = (status === 'All' || tr.dataset.status === status) ? '' : 'none';
    });
  }
  window.chipFilter = chipFilter;

  /* ── BOT CONTROLS ── */
  function startAnalysis(){
    if(botRunning) return;
    botRunning = true;
    var ss = document.getElementById('scanStatus');
    if(!ss) return;
    ss.style.display = 'block';
    ss.textContent = '> Connecting to AI engine...\n';

    var sel = document.getElementById('bot-market');
    var pair = sel ? sel.value : 'EUR/USD';

    fetch('/api/analyze-signal/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCSRF()},
      body: JSON.stringify({query_type: 'market_analysis', prompt: pair}),
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      ss.textContent += '> Analysis complete.\n';
      if(data.analysis){
        var trend = data.analysis.bias || 'neutral';
        var conf = data.analysis.confidence || 0;
        var tb = document.getElementById('trend-badge');
        if(tb){
          tb.className = 'trend-badge ' + trend;
          tb.textContent = trend === 'bullish' ? '▲ BULLISH' : trend === 'bearish' ? '▼ BEARISH' : '◆ SIDEWAYS';
        }
        var confVal = document.getElementById('conf-val');
        var confBar = document.getElementById('conf-bar');
        if(confVal) confVal.textContent = conf + '%';
        if(confBar) confBar.style.width = Math.min(conf, 100) + '%';
        ss.textContent += '> ' + (data.analysis.rationale || '') + '\n';
      }
      if(data.signal){
        ss.textContent += '> Signal: ' + data.signal.signal_type + ' (' + data.signal.confidence + '% confidence)\n';
        var sigEntry = document.getElementById('sig-entry');
        var sigSl = document.getElementById('sig-sl');
        var sigTp1 = document.getElementById('sig-tp1');
        if(sigEntry && data.signal.entry_price) sigEntry.textContent = data.signal.entry_price;
        if(sigSl && data.signal.stop_loss) sigSl.textContent = data.signal.stop_loss;
        if(sigTp1 && data.signal.take_profit) sigTp1.textContent = data.signal.take_profit;
      }
      ss.textContent += '> Awaiting user authorization to execute...\n';
      botRunning = false;
    })
    .catch(function(err){
      ss.textContent += '> Error: ' + err + '\n';
      botRunning = false;
    });
  }
  window.startAnalysis = startAnalysis;

  function stopAnalysis(){
    botRunning = false;
    var ss = document.getElementById('scanStatus');
    if(ss){
      ss.textContent += '> Analysis stopped.\n';
      setTimeout(function(){ss.style.display = 'none'}, 2000);
    }
  }
  window.stopAnalysis = stopAnalysis;

  function refreshSignals(){
    loadSignals();
    loadRiskConfig();
  }
  window.refreshSignals = refreshSignals;

  function loadSignals(){
    fetch('/api/signals/', {
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      var signals = data.results || data || [];
      if(signals.length > 0){
        var s = signals[0];
        var confVal = document.getElementById('conf-val');
        var confBar = document.getElementById('conf-bar');
        if(confVal) confVal.textContent = s.confidence + '%';
        if(confBar) confBar.style.width = Math.min(s.confidence, 100) + '%';
        var tb = document.getElementById('trend-badge');
        if(tb){
          var trend = s.signal_type === 'BUY' ? 'bullish' : s.signal_type === 'SELL' ? 'bearish' : 'sideways';
          tb.className = 'trend-badge ' + trend;
          tb.textContent = s.signal_type === 'BUY' ? '▲ BULLISH' : s.signal_type === 'SELL' ? '▼ BEARISH' : '◆ SIDEWAYS';
        }
        var sigEntry = document.getElementById('sig-entry');
        var sigSl = document.getElementById('sig-sl');
        var sigTp1 = document.getElementById('sig-tp1');
        if(sigEntry && s.entry_price) sigEntry.textContent = s.entry_price;
        if(sigSl && s.stop_loss) sigSl.textContent = s.stop_loss;
        if(sigTp1 && s.take_profit) sigTp1.textContent = s.take_profit;
        var reasoning = document.getElementById('scanStatus');
        if(reasoning && s.reasoning) reasoning.textContent = '> ' + s.reasoning;
      }
    })
    .catch(function(){});
  }

  function loadRiskConfig(){
    fetch('/api/risk-config/', {
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      var riskInput = document.querySelector('.risk-field input[type="number"]');
      if(riskInput && data.risk_per_trade !== undefined) riskInput.value = data.risk_per_trade;
      var maxTrades = document.querySelectorAll('.risk-field input[type="number"]')[1];
      if(maxTrades && data.max_daily_trades !== undefined) maxTrades.value = data.max_daily_trades;
      var drawdown = document.querySelectorAll('.risk-field input[type="number"]')[2];
      if(drawdown && data.max_drawdown !== undefined) drawdown.value = data.max_drawdown;
      var profitTarget = document.querySelectorAll('.risk-field input[type="number"]')[3];
      if(profitTarget && data.daily_profit_target !== undefined) profitTarget.value = data.daily_profit_target;
    })
    .catch(function(){});
  }

  function saveRiskConfig(){
    var inputs = document.querySelectorAll('.risk-field input[type="number"]');
    fetch('/api/risk-config/', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCSRF()},
      body: JSON.stringify({
        risk_per_trade: inputs[0] ? parseFloat(inputs[0].value) : 2.0,
        max_daily_trades: inputs[1] ? parseInt(inputs[1].value) : 5,
        max_drawdown: inputs[2] ? parseFloat(inputs[2].value) : 10.0,
        daily_profit_target: inputs[3] ? parseFloat(inputs[3].value) : 5.0,
      }),
    })
    .then(function(){ showToast('Risk settings saved', 'success'); })
    .catch(function(){ showToast('Failed to save risk settings', 'error'); });
  }
  window.saveRiskConfig = saveRiskConfig;

  function executeTrade(){
    var pair = document.getElementById('bot-market') ? document.getElementById('bot-market').value : 'EUR/USD';
    var entry = document.getElementById('sig-entry') ? document.getElementById('sig-entry').textContent : '0';
    var sl = document.getElementById('sig-sl') ? document.getElementById('sig-sl').textContent : '';
    var tp1 = document.getElementById('sig-tp1') ? document.getElementById('sig-tp1').textContent : '';

    var sel = document.getElementById('bot-tf');
    var volume = 0.01;

    if(!confirm('⚠️ TRADE CONFIRMATION\n\nPair: ' + pair + '\nEntry: ' + entry + '\n\nPlease confirm this trade. All trading carries substantial risk.')){
      return;
    }

    fetch('/api/trades/execute/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCSRF()},
      body: JSON.stringify({
        symbol: pair,
        action: 'BUY',
        volume: volume,
        entry_price: parseFloat(entry) || 0,
        stop_loss: parseFloat(sl) || null,
        take_profit: parseFloat(tp1) || null,
        order_type: 'MARKET',
      }),
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      if(data.id){
        showToast('✅ Trade executed — ID: ' + data.id + ' [' + data.status + ']', 'success');
      } else {
        showToast('❌ ' + (data.error || 'Execution failed'), 'error');
      }
    })
    .catch(function(err){
      showToast('❌ Network error: ' + err, 'error');
    });
  }
  window.executeTrade = executeTrade;

  function getCSRF(){
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }

  /* ── CHARTS ── */
  function initArtCanvas(){
    var canvas = document.getElementById('artCanvas');
    if(!canvas) return;
    canvas.width = canvas.offsetWidth * (window.devicePixelRatio || 1) || 800;
    var ctx = canvas.getContext('2d');
    var w = canvas.width, h = canvas.height;

    var pairsArt = ['EUR/USD','GBP/USD','XAU/USD','BTC/USD'];
    var series = {};
    pairsArt.forEach(function(p){
      series[p] = [];
      for(var i=0;i<40;i++) series[p].push(50 + Math.sin(i*0.2 + Math.random()) * 15 + Math.random() * 5);
    });
    var colors = ['#00d4aa','#f5c27a','#e8a94a','#ff4d6d'];

    function draw(){
      ctx.clearRect(0,0,w,h);
      var colW = w / pairsArt.length;
      pairsArt.forEach(function(p, pi){
        var ox = pi * colW, pts = series[p];
        var max = Math.max.apply(null, pts), min = Math.min.apply(null, pts);
        var scale = h / (max - min + 1);
        ctx.globalAlpha = 0.12;
        ctx.fillStyle = colors[pi];
        ctx.beginPath();
        ctx.moveTo(ox, h);
        pts.forEach(function(v, i){ctx.lineTo(ox + i * (colW / pts.length), h - (v - min) * scale)});
        ctx.lineTo(ox + colW, h);
        ctx.closePath();
        ctx.fill();
        ctx.globalAlpha = 1;
        ctx.strokeStyle = colors[pi];
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        pts.forEach(function(v, i){
          var x = ox + i * (colW / pts.length);
          var y = h - (v - min) * scale;
          i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        });
        ctx.stroke();
        ctx.fillStyle = 'rgba(255,255,255,0.5)';
        ctx.font = '10px DM Mono';
        ctx.fillText(p, ox + 6, 14);
      });
      pairsArt.forEach(function(p){
        series[p].shift();
        series[p].push(series[p][series[p].length-1] + (Math.random() - 0.48) * 3);
      });
      artAnimId = requestAnimationFrame(draw);
    }
    draw();
  }

  function initBotChart(){
    if (typeof LightweightCharts === 'undefined') return;
    var wrap = document.querySelector('.chart-canvas-wrap');
    if (!wrap) return;

    if (botChartInstance) {
      botChartInstance.remove();
      botChartInstance = null;
      botCandleSeries = null;
    }

    if (!botChartContainer || !document.body.contains(botChartContainer)) {
      botChartContainer = document.createElement('div');
      botChartContainer.id = 'tv-bot-chart';
      botChartContainer.style.width = '100%';
      botChartContainer.style.height = '260px';
      wrap.innerHTML = '';
      wrap.appendChild(botChartContainer);
    } else {
      wrap.innerHTML = '';
      wrap.appendChild(botChartContainer);
    }

    var sel = document.getElementById('bot-market');
    var pair = sel ? sel.value : 'EUR/USD';

    if (currentChartPair && currentChartPair !== pair) {
      removeWSListener(currentChartPair, botTickHandler);
      delete candleBuffer[currentChartPair];
    }
    currentChartPair = pair;

    botChartInstance = LightweightCharts.createChart(botChartContainer, {
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#8fa3bf',
        fontSize: 10,
        fontFamily: 'DM Mono, monospace',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Normal,
        vertLine: { color: 'rgba(245,194,122,0.4)', width: 1, style: LightweightCharts.LineStyle.Dashed },
        horzLine: { color: 'rgba(245,194,122,0.4)', width: 1, style: LightweightCharts.LineStyle.Dashed },
      },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.08)',
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.08)',
      },
    });

    botCandleSeries = botChartInstance.addCandlestickSeries({
      upColor: '#00d4aa',
      downColor: '#ff4d6d',
      borderUpColor: '#00d4aa',
      borderDownColor: '#ff4d6d',
      wickUpColor: '#00d4aa',
      wickDownColor: '#ff4d6d',
      priceFormat: {
        type: 'price',
        precision: pair.indexOf('JPY') !== -1 || pair.indexOf('BTC') !== -1 || pair.indexOf('XAU') !== -1 ? 2 : 5,
        minMove: pair.indexOf('JPY') !== -1 || pair.indexOf('BTC') !== -1 || pair.indexOf('XAU') !== -1 ? 0.01 : 0.00001,
      },
    });

    var basePrice = pairs[pair] || 1.08;
    var now = Math.floor(Date.now() / 1000);
    var seedCandles = [];
    var t = now - 120;
    for (var i = 0; i < 60; i++) {
      var o = basePrice * (1 + (Math.sin(i * 0.15 + 1) * 0.008) + (Math.sin(i * 0.3) * 0.004));
      var c = basePrice * (1 + (Math.sin((i + 1) * 0.15 + 1) * 0.008) + (Math.sin((i + 1) * 0.3) * 0.004));
      var high = Math.max(o, c) * 1.001;
      var low = Math.min(o, c) * 0.999;
      seedCandles.push({ time: t + i * 2, open: o, high: high, low: low, close: c });
    }
    botCandleSeries.setData(seedCandles);

    candleBuffer[pair] = { ticks: [], lastCandleTime: now - (now % 60) };

    addWSListener(pair, botTickHandler);

    botChartInstance.timeScale().fitContent();
  }

  var botTickHandler = function(tick) {
    var sel = document.getElementById('bot-market');
    var pair = sel ? sel.value : 'EUR/USD';
    if (tick.symbol !== pair) return;
    if (!candleBuffer[pair]) return;
    var buf = candleBuffer[pair];
    buf.ticks.push(tick);

    var tickTime = new Date(tick.timestamp).getTime();
    if (isNaN(tickTime)) tickTime = Date.now();
    var candleSec = Math.floor(tickTime / 1000);
    var candleStart = candleSec - (candleSec % 60);

    if (candleStart > buf.lastCandleTime) {
      if (buf.ticks.length > 1) {
        var prev = buf.ticks;
        var o = prev[0].price;
        var h = prev[0].price;
        var l = prev[0].price;
        prev.forEach(function(t) {
          if (t.price > h) h = t.price;
          if (t.price < l) l = t.price;
        });
        var c = prev[prev.length - 1].price;
        var candle = { time: buf.lastCandleTime, open: o, high: h, low: l, close: c };
        if (botCandleSeries) botCandleSeries.update(candle);
      }
      buf.lastCandleTime = candleStart;
      buf.ticks = [tick];
    } else {
      if (buf.ticks.length > 0) {
        var all = buf.ticks;
        var h = tick.price, l = tick.price;
        all.forEach(function(t) {
          if (t.price > h) h = t.price;
          if (t.price < l) l = t.price;
        });
        var partial = { time: buf.lastCandleTime, open: all[0].price, high: h, low: l, close: tick.price };
        if (botCandleSeries) botCandleSeries.update(partial);
      }
    }
  };

  function initAnalyticsCharts(){
    var pnlCanvas = document.getElementById('pnlChart');
    if(pnlCanvas){
      pnlCanvas.width = pnlCanvas.offsetWidth || 400;
      var ctx = pnlCanvas.getContext('2d');
      var w = pnlCanvas.width, h = pnlCanvas.height;
      var data = [];
      var running = 0;
      for(var i=0;i<30;i++){running += (Math.random()-0.4)*60; data.push(running)}
      var max = Math.max.apply(null, data);
      var min = Math.min.apply(null, data);
      var range = max - min || 1;
      ctx.clearRect(0,0,w,h);
      ctx.strokeStyle = '#00d4aa';
      ctx.lineWidth = 2;
      ctx.beginPath();
      data.forEach(function(v, i){
        var x = i * (w / (data.length-1));
        var y = h - 8 - (h - 16) * (v - min) / range;
        i === 0 ? ctx.moveTo(x,y) : ctx.lineTo(x,y);
      });
      ctx.stroke();
      ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
      var g = ctx.createLinearGradient(0,0,0,h);
      g.addColorStop(0, 'rgba(0,212,170,0.15)');
      g.addColorStop(1, 'rgba(0,212,170,0)');
      ctx.fillStyle = g;
      ctx.fill();
    }

    var winCanvas = document.getElementById('winChart');
    if(winCanvas){
      winCanvas.width = winCanvas.offsetWidth || 400;
      var ctx2 = winCanvas.getContext('2d');
      var w2 = winCanvas.width, h2 = winCanvas.height;
      ctx2.clearRect(0,0,w2,h2);
      var cx = w2/2, cy = h2/2, r = Math.min(w2,h2)/2 - 16;
      var winA = 0.684 * Math.PI * 2;
      ctx2.lineWidth = 20;
      ctx2.strokeStyle = 'rgba(255,255,255,0.05)';
      ctx2.beginPath(); ctx2.arc(cx, cy, r, 0, Math.PI*2); ctx2.stroke();
      ctx2.strokeStyle = '#00d4aa';
      ctx2.beginPath(); ctx2.arc(cx, cy, r, -Math.PI/2, -Math.PI/2 + winA); ctx2.stroke();
      ctx2.strokeStyle = '#ff4d6d';
      ctx2.beginPath(); ctx2.arc(cx, cy, r, -Math.PI/2 + winA, -Math.PI/2 + Math.PI*2); ctx2.stroke();
      ctx2.fillStyle = '#e8edf5';
      ctx2.font = 'bold 22px Syne';
      ctx2.textAlign = 'center';
      ctx2.fillText('68.4%', cx, cy+4);
      ctx2.fillStyle = 'rgba(255,255,255,0.4)';
      ctx2.font = '11px DM Mono';
      ctx2.fillText('WIN RATE', cx, cy+20);
    }
  }

  /* ── SETTINGS ── */
  function settingsTab(el, tab){
    document.querySelectorAll('.settings-nav-item').forEach(function(n){n.classList.remove('active')});
    el.classList.add('active');
    document.querySelectorAll('.settings-section').forEach(function(s){s.style.display = 'none'});
    var target = document.getElementById('settings-' + tab);
    if(target){target.style.display = 'block'}
  }
  window.settingsTab = settingsTab;

  /* ── TOAST NOTIFICATIONS ── */
  function showToast(message, type){
    type = type || 'info';
    var container = document.getElementById('toast-container');
    if(!container){
      container = document.createElement('div');
      container.className = 'toast-container';
      container.id = 'toast-container';
      document.body.appendChild(container);
    }
    var toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function(){
      toast.style.opacity = '0';
      toast.style.transition = 'opacity .3s ease';
      setTimeout(function(){toast.remove()}, 300);
    }, 4000);
  }
  window.showToast = showToast;

  /* ── SMOOTH SCROLL OBSERVER ── */
  function initScrollAnimations(){
    if(typeof IntersectionObserver === 'undefined') return;
    var observer = new IntersectionObserver(function(entries){
      entries.forEach(function(entry){
        if(entry.isIntersecting){
          entry.target.classList.add('fade-in-up');
          observer.unobserve(entry.target);
        }
      });
    }, {threshold: 0.1});

    document.querySelectorAll('.stat-card, .service-card, .plan-card, .market-card, .kpi-card').forEach(function(el){
      if(!el.classList.contains('fade-in-up')){
        el.style.opacity = '0';
        observer.observe(el);
      }
    });
  }

  /* ── NOTIFICATIONS ── */
  function toggleNotifications(){
    var panel = document.getElementById('notifPanel');
    if(!panel) return;
    var isOpen = panel.classList.contains('open');
    document.querySelectorAll('.notif-panel.open').forEach(function(p){p.classList.remove('open')});
    if(!isOpen){
      panel.classList.add('open');
      loadNotifications();
    }
  }
  window.toggleNotifications = toggleNotifications;

  function loadNotifications(){
    var list = document.getElementById('notifList');
    if(!list) return;
    var xhr = new XMLHttpRequest();
    xhr.open('GET', '/api/notifications/', true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.onload = function(){
      if(xhr.status === 200){
        var data = JSON.parse(xhr.responseText);
        var badge = document.getElementById('notifBadge');
        if(badge){
          if(data.unread_count > 0){
            badge.textContent = data.unread_count > 9 ? '9+' : data.unread_count;
            badge.classList.add('has-unread');
          } else {
            badge.textContent = '';
            badge.classList.remove('has-unread');
          }
        }
        if(data.notifications.length === 0){
          list.innerHTML = '<div class="notif-empty">No notifications yet.</div>';
          return;
        }
        var icons = {system:'📢', trade:'📊', signal:'⚡', account:'🔒'};
        list.innerHTML = data.notifications.map(function(n){
          var icon = icons[n.type] || '📢';
          var timeAgo = '';
          var d = new Date(n.created_at);
          var diff = Math.floor((Date.now() - d.getTime()) / 1000);
          if(diff < 60) timeAgo = 'just now';
          else if(diff < 3600) timeAgo = Math.floor(diff/60) + 'm ago';
          else if(diff < 86400) timeAgo = Math.floor(diff/3600) + 'h ago';
          else timeAgo = Math.floor(diff/86400) + 'd ago';
          return '<div class="notif-item' + (n.is_read ? '' : ' unread') + '" onclick="markAsRead(' + n.id + ')">' +
            '<div class="notif-item-icon ' + n.type + '">' + icon + '</div>' +
            '<div class="notif-item-content">' +
            '<div class="notif-item-title">' + escapeHtml(n.title) + '</div>' +
            (n.message ? '<div class="notif-item-msg">' + escapeHtml(n.message) + '</div>' : '') +
            '<div class="notif-item-time">' + timeAgo + '</div></div></div>';
        }).join('');
      }
    };
    xhr.send();
  }

  function escapeHtml(text){
    var d = document.createElement('div');
    d.textContent = text;
    return d.innerHTML;
  }

  function markAsRead(id){
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/notifications/' + id + '/read/', true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.onload = function(){
      if(xhr.status === 200){loadNotifications()}
    };
    xhr.send();
  }
  window.markAsRead = markAsRead;

  function markAllRead(){
    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/notifications/read-all/', true);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.setRequestHeader('Content-Type', 'application/x-www-form-urlencoded');
    xhr.onload = function(){
      if(xhr.status === 200){loadNotifications()}
    };
    xhr.send();
  }
  window.markAllRead = markAllRead;

  /* ── CLOSE NOTIFICATIONS ON CLICK OUTSIDE ── */
  document.addEventListener('click', function(e){
    var panel = document.getElementById('notifPanel');
    if(panel && panel.classList.contains('open')){
      var bell = document.getElementById('notifBell');
      if(!bell.contains(e.target) && !panel.contains(e.target)){
        panel.classList.remove('open');
      }
    }
  });

  /* ── NOTIFICATION POLLING ── */
  setInterval(function(){
    var panel = document.getElementById('notifPanel');
    if(!panel || !panel.classList.contains('open')){
      /* silent update badge only */
      var xhr = new XMLHttpRequest();
      xhr.open('GET', '/api/notifications/', true);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      xhr.onload = function(){
        if(xhr.status === 200){
          var data = JSON.parse(xhr.responseText);
          var badge = document.getElementById('notifBadge');
          if(badge){
            if(data.unread_count > 0){
              badge.textContent = data.unread_count > 9 ? '9+' : data.unread_count;
              badge.classList.add('has-unread');
            } else {
              badge.textContent = '';
              badge.classList.remove('has-unread');
            }
          }
        }
      };
      xhr.send();
    }
  }, 15000);

  /* ── INIT ── */
  function init(){
    initData();

    var appPage = document.getElementById('page-app');
    if(!appPage || !appPage.classList.contains('active')) return;

    navigateToPanel('dashboard');
    startTrialTimer();
    startPriceTicker();
    populateHistory();
    populateMarkets();
    setTimeout(function(){initArtCanvas(); initBotChart(); initAnalyticsCharts()}, 100);

    window.addEventListener('hashchange', handleHashChange);
    window.addEventListener('resize', function(){
      if(document.getElementById('page-app') && document.getElementById('page-app').classList.contains('active')){
        initArtCanvas(); initBotChart(); initAnalyticsCharts();
      }
    });

    setTimeout(initScrollAnimations, 500);
  }

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
