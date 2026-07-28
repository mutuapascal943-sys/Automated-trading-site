(function(){
  'use strict';

  let trialSeconds = 23*3600 + 47*60 + 12;
  let botRunning = false;
  let botInterval = null;
  let artAnimId = null;
  let pairs = {};
  let changes = {};
  let histData = [];
  let marketCategories = {};
  let currentMarketCategory = 'all';
  let currentMarketSearch = '';

  var wsConnections = {};
  var botChartInstance = null;
  var botCandleSeries = null;
  var candleBuffer = {};
  var currentChartPair = null;

  var botChartContainer = null;
  var marketChartInstances = [];

  var botEntryLine = null;
  var botSLLine = null;
  var botTPLine = null;
  var botEntryMarker = null;
  var botSLMarker = null;
  var botTPMarker = null;
  var lastProposedSignal = null;

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
    marketCategories = {
      'EUR/USD':'forex','GBP/USD':'forex','USD/JPY':'forex',
      'AUD/USD':'forex','USD/CAD':'forex','NZD/USD':'forex',
      'XAU/USD':'commodities',
      'BTC/USD':'crypto',
      'Boom 1000 Index':'indices','Crash 1000 Index':'indices',
      'Volatility 75 Index':'indices','Volatility 100 Index':'indices'
    };
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
    if(panelId === 'markets'){setTimeout(function(){populateMarkets(); updateMarketSummary()},50)}
    if(panelId === 'dashboard'){fetchDashboardStats(); if(!artAnimId){initArtCanvas()}}
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
  function populateMarkets(filterCat, filterText){
    filterCat = filterCat || currentMarketCategory;
    filterText = (filterText || currentMarketSearch).toLowerCase();
    var grid = document.getElementById('markets-grid');
    if(!grid) return;

    var keys = Object.keys(pairs).filter(function(k){
      if (filterCat !== 'all' && marketCategories[k] !== filterCat) return false;
      if (filterText && !k.toLowerCase().includes(filterText)) return false;
      return true;
    });

    grid.innerHTML = keys.map(function(k){
      var p = pairs[k];
      var pStr = p > 100 ? p.toFixed(2) : p.toFixed(5);
      var cls = (changes[k] || 0) >= 0 ? 'up' : 'down';
      var sign = (changes[k] || 0) >= 0 ? '+' : '';
      var changeVal = (changes[k] || 0).toFixed(2);
      var cat = marketCategories[k] || '';
      var spread = (p * 0.0002).toFixed(p > 100 ? 2 : 5);
      var bid = (p * 0.9998).toFixed(p > 100 ? 2 : 5);
      var ask = (p * 1.0002).toFixed(p > 100 ? 2 : 5);
      var dailyHigh = (p * 1.003).toFixed(p > 100 ? 2 : 5);
      var dailyLow = (p * 0.997).toFixed(p > 100 ? 2 : 5);
      return '<div class="market-card ' + cls + '" onclick="navigateToBot(\'' + k + '\')">' +
        '<div class="market-pair">' + k + '<span class="market-category-badge">' + cat + '</span></div>' +
        '<div class="market-price-row">' +
        '<span class="market-price">' + pStr + '</span>' +
        '<span class="market-change ' + cls + '">' + sign + changeVal + '%</span></div>' +
        '<div class="market-details">' +
        '<div class="mkt-detail"><span class="mkt-detail-label">Bid</span><span class="mkt-detail-value">' + bid + '</span></div>' +
        '<div class="mkt-detail"><span class="mkt-detail-label">Ask</span><span class="mkt-detail-value">' + ask + '</span></div>' +
        '<div class="mkt-detail"><span class="mkt-detail-label">Spread</span><span class="mkt-detail-value">' + spread + '</span></div>' +
        '<div class="mkt-detail"><span class="mkt-detail-label">Day Range</span><span class="mkt-detail-value">' + dailyLow + ' – ' + dailyHigh + '</span></div>' +
        '</div></div>';
    }).join('');
  }

  function filterMarkets(text){
    currentMarketSearch = text;
    populateMarkets(currentMarketCategory, text);
  }
  window.filterMarkets = filterMarkets;

  function filterMarketCategory(el){
    document.querySelectorAll('.mkt-cat-chip').forEach(function(c){c.classList.remove('active')});
    el.classList.add('active');
    currentMarketCategory = el.dataset.cat;
    populateMarkets(currentMarketCategory, currentMarketSearch);
  }
  window.filterMarketCategory = filterMarketCategory;

  function updateMarketSummary(){
    var upCount = 0, downCount = 0;
    var bestKey = null, bestChange = -999;
    var worstKey = null, worstChange = 999;
    Object.keys(changes).forEach(function(k){
      var c = changes[k] || 0;
      if (c >= 0) upCount++; else downCount++;
      if (c > bestChange) { bestChange = c; bestKey = k; }
      if (c < worstChange) { worstChange = c; worstKey = k; }
    });
    var bestEl = document.getElementById('top-gainer');
    if (bestEl && bestKey) bestEl.textContent = bestKey;
    var bestChangeEl = document.getElementById('top-gainer-change');
    if (bestChangeEl) bestChangeEl.textContent = '+' + bestChange.toFixed(2) + '%';

    var worstEl = document.getElementById('top-loser');
    if (worstEl && worstKey) worstEl.textContent = worstKey;
    var worstChangeEl = document.getElementById('top-loser-change');
    if (worstChangeEl) worstChangeEl.textContent = worstChange.toFixed(2) + '%';

    var advEl = document.getElementById('advancers');
    if (advEl) advEl.textContent = upCount;

    var decEl = document.getElementById('decliners');
    if (decEl) decEl.textContent = downCount;
  }

  function navigateToBot(pair){
    var sel = document.getElementById('bot-market');
    if(sel){sel.value = pair; updateBotMarket()}
    navigateToPanel('bot');
  }
  window.navigateToBot = navigateToBot;

  function updateMarketCards(){
    Object.keys(pairs).forEach(function(k){
      var p = pairs[k];
      var pStr = p > 100 ? p.toFixed(2) : p.toFixed(5);
      var cls = (changes[k] || 0) >= 0 ? 'up' : 'down';
      var sign = (changes[k] || 0) >= 0 ? '+' : '';
      var changeVal = (changes[k] || 0).toFixed(2);
      var spread = (p * 0.0002).toFixed(p > 100 ? 2 : 5);
      var bid = (p * 0.9998).toFixed(p > 100 ? 2 : 5);
      var ask = (p * 1.0002).toFixed(p > 100 ? 2 : 5);
      var cards = document.querySelectorAll('.market-card');
      for(var i=0;i<cards.length;i++){
        var pairEl = cards[i].querySelector('.market-pair');
        if(pairEl && pairEl.textContent.trim().startsWith(k)){
          var priceEl = cards[i].querySelector('.market-price');
          if(priceEl) priceEl.textContent = pStr;
          var changeEl = cards[i].querySelector('.market-change');
          if(changeEl) changeEl.textContent = sign + changeVal + '%';
          cards[i].className = 'market-card ' + cls;
          var details = cards[i].querySelectorAll('.mkt-detail-value');
          if(details.length >= 4){
            details[0].textContent = bid;
            details[1].textContent = ask;
            details[2].textContent = spread;
          }
        }
      }
    });
    updateMarketSummary();
  }

  function updateBotMarket(){
    var sel = document.getElementById('bot-market');
    var pair = sel ? sel.value : 'EUR/USD';
    var labelEl = document.getElementById('bot-pair-label');
    if(labelEl){labelEl.textContent = pair}
    var p = pairs[pair] || 1.0;
    var priceEl = document.getElementById('bot-price');
    if(priceEl){priceEl.textContent = p > 100 ? p.toFixed(2) : p.toFixed(5)}
    clearChartOverlay();
    var stateBadge = document.getElementById('signal-state-badge');
    if(stateBadge){ stateBadge.textContent = 'NO SIGNAL'; stateBadge.className = 'signal-state-badge'; }
    var emptyBody = document.getElementById('signal-body-empty');
    var contentBody = document.getElementById('signal-body-content');
    if(emptyBody) emptyBody.style.display = 'block';
    if(contentBody) contentBody.style.display = 'none';
    lastProposedSignal = null;
    initBotChart();
  }
  window.updateBotMarket = updateBotMarket;

  function updateSignals(pair, price){
    /* Kept for backward compatibility — SL/TP now come from backend */
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
    ss.textContent = '> Running technical analysis...\n';

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
      var bias = 'neutral';
      var conf = 0;
      if(data.analysis){
        bias = data.analysis.bias || 'neutral';
        conf = data.analysis.confidence || 0;
        var tb = document.getElementById('trend-badge');
        if(tb){
          tb.className = 'trend-badge ' + bias;
          tb.textContent = bias === 'bullish' ? 'BULLISH' : bias === 'bearish' ? 'BEARISH' : 'SIDEWAYS';
        }
        var confVal = document.getElementById('conf-val');
        var confBar = document.getElementById('conf-bar');
        if(confVal) confVal.textContent = conf + '%';
        if(confBar) confBar.style.width = Math.min(conf, 100) + '%';
        if(data.analysis.rsi) ss.textContent += '> RSI: ' + data.analysis.rsi + '\n';
        if(data.analysis.atr) ss.textContent += '> ATR: ' + data.analysis.atr + '\n';
        ss.textContent += '> ' + (data.analysis.rationale || '') + '\n';
      }

      var signalType = bias === 'bullish' ? 'BUY' : bias === 'bearish' ? 'SELL' : 'BUY';
      var currentPrice = pairs[pair] || 0;

      return fetch('/api/signal/propose/', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCSRF()},
        body: JSON.stringify({
          symbol: pair,
          signal_type: signalType,
          confidence: conf / 100,
          current_price: currentPrice,
        }),
      });
    })
    .then(function(r){ return r.json() })
    .then(function(proposal){
      ss.textContent += '> Bot calculated SL/TP (ATR-based)\n';
      ss.textContent += '> ' + proposal.reasoning + '\n';

      lastProposedSignal = proposal;

      var sigEntry = document.getElementById('sig-entry');
      var sigSl = document.getElementById('sig-sl');
      var sigTp = document.getElementById('sig-tp');
      var sigSlDist = document.getElementById('sig-sl-dist');
      var sigTpDist = document.getElementById('sig-tp-dist');
      var sigAtr = document.getElementById('sig-atr');
      var sigRr = document.getElementById('sig-rr');
      var sigStake = document.getElementById('sig-stake');
      var reasoningEl = document.getElementById('reasoning-text');
      var stateBadge = document.getElementById('signal-state-badge');
      var emptyBody = document.getElementById('signal-body-empty');
      var contentBody = document.getElementById('signal-body-content');

      if(sigEntry) sigEntry.textContent = proposal.current_price;
      if(sigSl) sigSl.textContent = proposal.stop_loss;
      if(sigTp) sigTp.textContent = proposal.take_profit;
      if(sigSlDist) sigSlDist.textContent = proposal.sl_distance;
      if(sigTpDist) sigTpDist.textContent = proposal.tp_distance;
      if(sigAtr) sigAtr.textContent = proposal.atr;
      if(sigRr){
        var slD = parseFloat(proposal.sl_distance) || 1;
        var tpD = parseFloat(proposal.tp_distance) || 1;
        sigRr.textContent = (tpD / slD).toFixed(2) + ':1';
      }
      if(sigStake) sigStake.textContent = '$' + proposal.stake_amount;
      if(reasoningEl) reasoningEl.textContent = proposal.reasoning;
      if(stateBadge){
        stateBadge.textContent = 'PROPOSED';
        stateBadge.className = 'signal-state-badge proposed';
      }
      if(emptyBody) emptyBody.style.display = 'none';
      if(contentBody) contentBody.style.display = 'block';

      updateChartOverlay(proposal);

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
          tb.textContent = s.signal_type === 'BUY' ? 'BULLISH' : s.signal_type === 'SELL' ? 'BEARISH' : 'SIDEWAYS';
        }
        if(s.entry_price){
          var proposal = {
            current_price: s.entry_price,
            stop_loss: s.stop_loss || '0',
            take_profit: s.take_profit || '0',
            sl_distance: '0',
            tp_distance: '0',
            atr: '0',
            reasoning: s.reasoning || '',
            confidence: s.confidence / 100,
            stake_amount: '0',
          };
          lastProposedSignal = proposal;
          var sigEntry = document.getElementById('sig-entry');
          var sigSl = document.getElementById('sig-sl');
          var sigTp = document.getElementById('sig-tp');
          var reasoningEl = document.getElementById('reasoning-text');
          var stateBadge = document.getElementById('signal-state-badge');
          var emptyBody = document.getElementById('signal-body-empty');
          var contentBody = document.getElementById('signal-body-content');
          if(sigEntry) sigEntry.textContent = s.entry_price;
          if(sigSl) sigSl.textContent = s.stop_loss;
          if(sigTp) sigTp.textContent = s.take_profit;
          if(reasoningEl && s.reasoning) reasoningEl.textContent = s.reasoning;
          if(stateBadge){
            stateBadge.textContent = s.is_executed ? 'EXECUTED' : 'PROPOSED';
            stateBadge.className = 'signal-state-badge ' + (s.is_executed ? 'executed' : 'proposed');
          }
          if(emptyBody) emptyBody.style.display = 'none';
          if(contentBody) contentBody.style.display = 'block';
          if(s.stop_loss && s.take_profit) updateChartOverlay(proposal);
        }
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

  function loadStakeConfig(){
    fetch('/api/stake-config/', {
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      var input = document.getElementById('stake-amount');
      if(input && data.stake_amount) input.value = data.stake_amount;
    })
    .catch(function(){});
  }
  window.loadStakeConfig = loadStakeConfig;

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
    var tp = document.getElementById('sig-tp') ? document.getElementById('sig-tp').textContent : '';
    var stakeInput = document.getElementById('stake-amount');
    var stake = stakeInput ? parseFloat(stakeInput.value) : 0.01;

    var tb = document.getElementById('trend-badge');
    var trendText = tb ? tb.textContent.trim() : '';
    var action = 'BUY';
    if(trendText.indexOf('BEARISH') !== -1) action = 'SELL';

    var conf = 0;
    if(lastProposedSignal && lastProposedSignal.confidence) conf = lastProposedSignal.confidence;

    if(!confirm('TRADE CONFIRMATION\n\nPair: ' + pair + '\nDirection: ' + action + '\nStake: $' + stake + '\nEntry: ' + entry + '\nStop Loss: ' + sl + '\nTake Profit: ' + tp + '\n\nPlease confirm this trade.')){
      return;
    }

    fetch('/api/trades/execute/', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': getCSRF()},
      body: JSON.stringify({
        symbol: pair,
        action: action,
        volume: stake,
        entry_price: parseFloat(entry) || 0,
        stop_loss: parseFloat(sl) || null,
        take_profit: parseFloat(tp) || null,
        order_type: 'MARKET',
        confidence: conf,
        current_price: pairs[pair] || 0,
      }),
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      if(data.id){
        showToast('Trade executed -- ID: ' + data.id + ' [' + data.status + ']', 'success');
        var stateBadge = document.getElementById('signal-state-badge');
        if(stateBadge){
          stateBadge.textContent = data.status;
          stateBadge.className = 'signal-state-badge ' + data.status.toLowerCase();
        }
        loadRecentTrades();
      } else {
        showToast(data.error || 'Execution failed', 'error');
      }
    })
    .catch(function(err){
      showToast('Network error: ' + err, 'error');
    });
  }
  window.executeTrade = executeTrade;

  function getCSRF(){
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : '';
  }
  window.getCSRF = getCSRF;

  /* ── CHARTS ── */
  function initArtCanvas(){
    if (typeof LightweightCharts === 'undefined') return;
    var grid = document.getElementById('marketChartGrid');
    if (!grid) return;
    if (artAnimId) { return; }
    artAnimId = true;

    var overviewPairs = Object.keys(pairs);
    var lineSeries = {};

    grid.innerHTML = '';
    overviewPairs.forEach(function(sym){
      var wrap = document.createElement('div');
      wrap.className = 'mini-chart-wrap';
      wrap.innerHTML = '<div class="mini-chart-label">' + sym + '</div><div class="mini-chart-container" id="mc-' + sym.replace(/[^a-zA-Z0-9]/g, '_') + '"></div>';
      grid.appendChild(wrap);
    });

    overviewPairs.forEach(function(sym){
      var safeId = 'mc-' + sym.replace(/[^a-zA-Z0-9]/g, '_');
      var container = document.getElementById(safeId);
      if (!container) return;

      var precision = (sym.indexOf('JPY') !== -1 || sym.indexOf('BTC') !== -1 || sym.indexOf('XAU') !== -1) ? 2 : 5;

      var chart = LightweightCharts.createChart(container, {
        layout: {
          background: { type: 'solid', color: 'transparent' },
          textColor: '#8fa3bf',
          fontSize: 9,
          fontFamily: 'DM Mono, monospace',
        },
        grid: {
          vertLines: { color: 'rgba(255,255,255,0.04)' },
          horzLines: { color: 'rgba(255,255,255,0.04)' },
        },
        timeScale: { visible: false, borderColor: 'rgba(255,255,255,0.08)' },
        rightPriceScale: { visible: false, borderColor: 'rgba(255,255,255,0.08)' },
        crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
        handleScroll: false,
        handleScale: false,
      });

      var series = chart.addLineSeries({
        color: '#00d4aa',
        lineWidth: 1.5,
        priceFormat: { type: 'price', precision: precision, minMove: precision === 2 ? 0.01 : 0.00001 },
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        priceLineVisible: false,
      });

      var basePrice = pairs[sym] || 1.0;
      var now = Math.floor(Date.now() / 1000);
      var seedData = [];
      for (var i = 60; i >= 1; i--) {
        var noise = (Math.sin(i * 0.3 + 1) * 0.006) + (Math.sin(i * 0.7) * 0.003) + (Math.random() - 0.5) * 0.002;
        seedData.push({ time: now - i * 2, value: basePrice * (1 + noise) });
      }
      series.setData(seedData);
      chart.timeScale().fitContent();

      marketChartInstances.push(chart);
      lineSeries[sym] = series;

      addWSListener(sym, function(tick){
        var s = lineSeries[tick.symbol];
        if (!s) return;
        var t = Math.floor(new Date(tick.timestamp).getTime() / 1000);
        if (isNaN(t)) { t = Math.floor(Date.now() / 1000); }
        s.update({ time: t, value: tick.price });
      });
    });
  }

  function initBotChart(){
    if (typeof LightweightCharts === 'undefined') return;
    var wrap = document.querySelector('.chart-canvas-wrap');
    if (!wrap) return;

    if (botChartInstance) {
      botEntryLine = null; botSLLine = null; botTPLine = null;
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

  function updateChartOverlay(proposal){
    if(!botChartInstance || !botCandleSeries) return;

    if(botEntryLine){ try{ botChartInstance.removeSeries(botEntryLine); }catch(e){} botEntryLine = null; }
    if(botSLLine){ try{ botChartInstance.removeSeries(botSLLine); }catch(e){} botSLLine = null; }
    if(botTPLine){ try{ botChartInstance.removeSeries(botTPLine); }catch(e){} botTPLine = null; }

    var entry = parseFloat(proposal.current_price);
    var sl = parseFloat(proposal.stop_loss);
    var tp = parseFloat(proposal.take_profit);

    if(isNaN(entry) || isNaN(sl) || isNaN(tp)) return;

    botEntryLine = botChartInstance.addLineSeries({
      color: '#00d4aa',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    botEntryLine.setData([
      {time: Math.floor(Date.now()/1000) - 3600 * 5, value: entry},
      {time: Math.floor(Date.now()/1000), value: entry},
    ]);

    botSLLine = botChartInstance.addLineSeries({
      color: '#ff4d6d',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    botSLLine.setData([
      {time: Math.floor(Date.now()/1000) - 3600 * 5, value: sl},
      {time: Math.floor(Date.now()/1000), value: sl},
    ]);

    botTPLine = botChartInstance.addLineSeries({
      color: '#f5c27a',
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false,
    });
    botTPLine.setData([
      {time: Math.floor(Date.now()/1000) - 3600 * 5, value: tp},
      {time: Math.floor(Date.now()/1000), value: tp},
    ]);

    var legend = document.getElementById('chart-levels-legend');
    if(legend) legend.style.display = 'flex';
  }
  window.updateChartOverlay = updateChartOverlay;

  function clearChartOverlay(){
    if(botEntryLine){ try{ botChartInstance.removeSeries(botEntryLine); }catch(e){} botEntryLine = null; }
    if(botSLLine){ try{ botChartInstance.removeSeries(botSLLine); }catch(e){} botSLLine = null; }
    if(botTPLine){ try{ botChartInstance.removeSeries(botTPLine); }catch(e){} botTPLine = null; }
    var legend = document.getElementById('chart-levels-legend');
    if(legend) legend.style.display = 'none';
  }

  function fetchDashboardStats(){
    fetch('/api/dashboard/stats/', {
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      function setText(id, val){
        var el = document.getElementById(id);
        if(el) el.textContent = val;
      }

      setText('hero-balance', '$' + (data.balance || 0).toLocaleString(undefined, {minimumFractionDigits:0}));
      setText('hero-trades', data.open_trades || 0);
      setText('hero-signals', (Array.isArray(data.recent_signals) ? data.recent_signals.length : data.recent_signals) || 0);

      setText('dash-balance', '$' + (data.balance || 0).toLocaleString(undefined, {minimumFractionDigits:0}));
      setText('dash-win-rate', (data.win_rate || 0) + '%');
      setText('dash-open-trades', data.open_trades || 0);
      setText('dash-monthly-pnl', (data.monthly_pnl >= 0 ? '+$' : '-$') + Math.abs(data.monthly_pnl || 0).toFixed(2));
      setText('dash-signals', (Array.isArray(data.recent_signals) ? data.recent_signals.length : data.recent_signals) || 0);

      var dailyPnl = data.daily_pnl || 0;
      var balDelta = document.getElementById('dash-balance-delta');
      if(balDelta) balDelta.textContent = (dailyPnl >= 0 ? '▲ +$' : '▼ -$') + Math.abs(dailyPnl).toFixed(2) + ' today';

      ['dash-win-rate', 'dash-monthly-pnl'].forEach(function(id){
        var el = document.getElementById(id);
        if(!el) return;
        var val = parseFloat(el.textContent.replace(/[^-.\d]/g, ''));
        if(val < 0) el.className = 'dash-kpi-value text-red';
        else if(val > 0) el.className = 'dash-kpi-value text-teal';
        else el.className = 'dash-kpi-value';
      });
    })
    .catch(function(){});
  }

  function initAnalyticsCharts(){
    fetch('/api/dashboard/stats/', {
      headers: {'X-Requested-With': 'XMLHttpRequest'},
    })
    .then(function(r){ return r.json() })
    .then(function(data){
      function setText(id, val, prefix, suffix){
        var el = document.getElementById(id);
        if(!el) return;
        prefix = prefix || '';
        suffix = suffix || '';
        el.textContent = prefix + val + suffix;
      }

      setText('kpi-win-rate', data.win_rate + '%');
      setText('kpi-loss-rate', data.loss_rate + '%');
      setText('kpi-profit-factor', data.profit_factor);
      setText('kpi-avg-trade', (data.avg_trade >= 0 ? '+$' : '-$') + Math.abs(data.avg_trade).toFixed(2));
      setText('kpi-daily-pnl', (data.daily_pnl >= 0 ? '+$' : '-$') + Math.abs(data.daily_pnl).toFixed(2));
      setText('kpi-weekly-pnl', (data.weekly_pnl >= 0 ? '+$' : '-$') + Math.abs(data.weekly_pnl).toFixed(2));
      setText('kpi-monthly-pnl', (data.monthly_pnl >= 0 ? '+$' : '-$') + Math.abs(data.monthly_pnl).toFixed(2));

      document.querySelectorAll('#kpi-daily-pnl, #kpi-weekly-pnl, #kpi-monthly-pnl').forEach(function(el){
        var val = parseFloat(el.textContent.replace(/[^-.\d]/g, ''));
        if(val < 0) el.className = 'kpi-value text-red';
        else if(val > 0) el.className = 'kpi-value text-teal';
      });
      var avgEl = document.getElementById('kpi-avg-trade');
      if(avgEl){
        var avgVal = parseFloat(avgEl.textContent.replace(/[^-.\d]/g, ''));
        if(avgVal < 0) avgEl.className = 'kpi-value text-red';
        else if(avgVal > 0) avgEl.className = 'kpi-value text-teal';
      }

      /* ── PnL Line Chart ── */
      if (typeof LightweightCharts !== 'undefined') {
        var pnlContainer = document.getElementById('pnlChartContainer');
        if (pnlContainer) {
          pnlContainer.innerHTML = '';
          var pnlChart = LightweightCharts.createChart(pnlContainer, {
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
            timeScale: {
              borderColor: 'rgba(255,255,255,0.08)',
              timeVisible: false,
              tickMarkFormatter: function(ts){ var d=new Date(ts*1000); return d.getDate()+'/'+(d.getMonth()+1); },
            },
            rightPriceScale: {
              borderColor: 'rgba(255,255,255,0.08)',
            },
            crosshair: {
              vertLine: { color: 'rgba(245,194,122,0.3)', width: 1, style: LightweightCharts.LineStyle.Dashed, labelVisible: false },
              horzLine: { color: 'rgba(245,194,122,0.3)', width: 1, style: LightweightCharts.LineStyle.Dashed, labelVisible: false },
            },
            handleScroll: false,
            handleScale: false,
          });

          var pnlSeries = pnlChart.addAreaSeries({
            lineColor: '#00d4aa',
            topColor: 'rgba(0,212,170,0.15)',
            bottomColor: 'rgba(0,212,170,0)',
            lineWidth: 2,
            priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
          });

          var history = data.pnl_history || [];
          var chartData = [];
          var baseTime = Math.floor(Date.now() / 1000) - 30 * 86400;
          if (history.length > 0) {
            history.forEach(function(entry){
              var t = Math.floor(new Date(entry.date + 'T00:00:00').getTime() / 1000);
              if (!isNaN(t)) chartData.push({ time: t, value: entry.pnl });
            });
          }
          if (chartData.length < 2) {
            for (var i = 30; i >= 0; i--) {
              chartData.push({ time: baseTime + i * 86400, value: 0 });
            }
          }
          pnlSeries.setData(chartData);
          pnlChart.timeScale().fitContent();
        }

        /* ── Win/Loss Donut (Canvas) ── */
        var winContainer = document.getElementById('winChartContainer');
        if (winContainer) {
          winContainer.innerHTML = '<canvas id="winChart" style="width:100%;height:100%"></canvas>';
          var winCanvas = document.getElementById('winChart');
          if (winCanvas) {
            var dpr = window.devicePixelRatio || 1;
            var rect = winContainer.getBoundingClientRect();
            winCanvas.width = rect.width * dpr;
            winCanvas.height = rect.height * dpr;
            winCanvas.style.width = rect.width + 'px';
            winCanvas.style.height = rect.height + 'px';
            var ctx = winCanvas.getContext('2d');
            var w = winCanvas.width, h = winCanvas.height;
            var cx = w / 2, cy = h / 2, r = Math.min(w, h) / 2 - 24 * dpr;

            ctx.clearRect(0, 0, w, h);

            var winPct = data.win_rate || 0;
            var lossPct = data.loss_rate || 0;
            var winAngle = (winPct / 100) * Math.PI * 2;
            var lossAngle = (lossPct / 100) * Math.PI * 2;

            var lineW = 18 * dpr;
            ctx.lineCap = 'round';

            ctx.lineWidth = lineW;
            ctx.strokeStyle = 'rgba(255,255,255,0.04)';
            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2);
            ctx.stroke();

            if (winPct > 0) {
              ctx.strokeStyle = '#00d4aa';
              ctx.lineWidth = lineW;
              ctx.beginPath();
              ctx.arc(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + winAngle);
              ctx.stroke();
            }

            if (lossPct > 0) {
              ctx.strokeStyle = '#ff4d6d';
              ctx.lineWidth = lineW;
              ctx.beginPath();
              ctx.arc(cx, cy, r, -Math.PI / 2 + winAngle, -Math.PI / 2 + Math.PI * 2);
              ctx.stroke();
            }

            ctx.fillStyle = '#e8edf5';
            ctx.font = 'bold ' + (22 * dpr) + 'px Syne';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText(winPct + '%', cx, cy - 6 * dpr);

            ctx.fillStyle = 'rgba(255,255,255,0.35)';
            ctx.font = (11 * dpr) + 'px DM Mono';
            ctx.fillText('WIN RATE', cx, cy + 14 * dpr);
          }
        }
      }
    })
    .catch(function(){
      /* fallback to zeros on error */
    });
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

    document.querySelectorAll('.stat-card, .service-card, .plan-card, .market-card, .kpi-card, .dashboard-kpi-card, .mkt-summary-card').forEach(function(el){
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
        var icons = {system:'campaign', trade:'candlestick_chart', signal:'bolt', account:'lock'};
        list.innerHTML = data.notifications.map(function(n){
          var icon = icons[n.type] || 'notifications';
          var timeAgo = '';
          var d = new Date(n.created_at);
          var diff = Math.floor((Date.now() - d.getTime()) / 1000);
          if(diff < 60) timeAgo = 'just now';
          else if(diff < 3600) timeAgo = Math.floor(diff/60) + 'm ago';
          else if(diff < 86400) timeAgo = Math.floor(diff/3600) + 'h ago';
          else timeAgo = Math.floor(diff/86400) + 'd ago';
          return '<div class="notif-item' + (n.is_read ? '' : ' unread') + '" onclick="markAsRead(' + n.id + ')">' +
            '<div class="notif-item-icon ' + n.type + '"><span class="material-symbols-outlined" style="font-size:16px">' + icon + '</span></div>' +
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
    xhr.setRequestHeader('X-CSRFToken', getCSRF());
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
    xhr.setRequestHeader('X-CSRFToken', getCSRF());
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
    updateMarketSummary();
    fetchDashboardStats();
    setTimeout(function(){initArtCanvas(); initBotChart(); initAnalyticsCharts()}, 100);

    window.addEventListener('hashchange', handleHashChange);
    window.addEventListener('resize', function(){
      if(document.getElementById('page-app') && document.getElementById('page-app').classList.contains('active')){
        marketChartInstances.forEach(function(c){ c.resize(c.container().clientWidth, c.container().clientHeight); });
        initBotChart(); initAnalyticsCharts();
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
