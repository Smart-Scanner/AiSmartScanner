
let ptTrades = [];
let ptStats = {};

function switchPTTab(tab, btn) {
    document.querySelectorAll('#ptTabs .v3-tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('ptOpen').style.display = tab === 'open' ? 'flex' : 'none';
    document.getElementById('ptClosed').style.display = tab === 'closed' ? 'flex' : 'none';
}

function calcConfidence(s) {
    const tech100 = Math.min(100, (s.technical_score || 0) / 30 * 100);
    const earn100 = Math.min(100, (s.earnings_momentum_score || 0) / 15 * 100);
    const sm100 = s.smart_money_100 || Math.min(100, (s.smart_money_score || 0) / 10 * 100);
    const fund100 = Math.min(100, (s.fundamental_score || 0) / 10 * 100);
    return Math.round(Math.min(100, Math.max(0, tech100 * 0.30 + earn100 * 0.25 + sm100 * 0.25 + fund100 * 0.20)));
}

function renderTradeRow(t, isOpen) {
    const entryPrice = t.entry_price || 0;
    const exitPrice = t.exit_price || entryPrice;
    const returnPct = t.return_pct || 0;
    const pnlClass = returnPct >= 0 ? 'positive' : 'negative';
    const pnlSign = returnPct >= 0 ? '+' : '';
    const badges = [];
    if (t.high_conviction) badges.push('<span class="pt-badge hc">HC</span>');
    if (t.is_golden) badges.push('<span class="pt-badge golden">GOLDEN</span>');
    const confScore = calcConfidence(t);
    const cl = confScore >= 80 ? 'high' : confScore >= 50 ? 'medium' : 'low';
    const sc = (t.score_at_entry||0) >= 80 ? 'excellent' : (t.score_at_entry||0) >= 65 ? 'good' : (t.score_at_entry||0) >= 50 ? 'average' : 'poor';

    if (isOpen) {
        const liveReturn = t.live_return_pct || 0;
        const livePnlClass = liveReturn >= 0 ? 'positive' : 'negative';
        const livePnlSign = liveReturn >= 0 ? '+' : '';
        const currentPrice = t.current_price || 0;
        const dayChg = t.day_change_pct || 0;
        return `<div class="pt-trade-row" onclick="openDrawerForSymbol('${t.symbol}')">
            <div>
                <div class="pt-trade-sym">${t.symbol}</div>
                <div class="pt-trade-sector">${t.sector || ''}</div>
                <div class="pt-badges" style="margin-top:4px">${badges.join('')}</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Entry</div>
                <div class="pt-trade-meta">\u20b9${entryPrice.toFixed(2)}</div>
                <div style="font-size:10px;color:var(--text-muted)">${t.entry_date||''}</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">LTP</div>
                <div class="pt-trade-meta" style="color:${currentPrice ? (liveReturn >= 0 ? 'var(--success)' : 'var(--danger)') : 'var(--text-muted)'}">${currentPrice ? '\u20b9'+currentPrice.toFixed(2) : '—'}</div>
                <div style="font-size:10px;color:${dayChg >= 0 ? 'var(--success)' : 'var(--danger)'}">${dayChg ? (dayChg >= 0 ? '+' : '') + dayChg.toFixed(1) + '% today' : ''}</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Live P&L</div>
                <div class="pt-pnl ${livePnlClass}" style="font-size:14px;font-weight:700">${currentPrice ? livePnlSign + liveReturn.toFixed(1) + '%' : '—'}</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Target / Stop</div>
                <div style="font-size:11px;color:var(--success)">\u20b9${(t.target_price||0).toFixed(0)}</div>
                <div style="font-size:11px;color:var(--danger)">\u20b9${(t.stop_loss||0).toFixed(0)}</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Score</div>
                <div><span class="v3-score ${sc}" style="font-size:12px;min-width:32px;height:24px">${t.score_at_entry||0}</span></div>
            </div>
        </div>`;
    } else {
        return `<div class="pt-trade-row" onclick="openDrawerForSymbol('${t.symbol}')">
            <div>
                <div class="pt-trade-sym">${t.symbol}</div>
                <div class="pt-trade-sector">${t.sector || ''}</div>
                <div class="pt-badges" style="margin-top:4px">
                    ${badges.join('')}
                    <span class="pt-badge ${returnPct >= 0 ? 'win' : 'loss'}">${returnPct >= 0 ? 'WIN' : 'LOSS'}</span>
                </div>
            </div>
            <div>
                <div class="pt-trade-meta-label">P&L</div>
                <div class="pt-pnl ${pnlClass}">${pnlSign}${returnPct.toFixed(1)}%</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Confidence</div>
                <div><span class="tp-conf ${cl}" style="font-size:10px;padding:2px 6px">${confScore}%</span></div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Alpha</div>
                <div class="pt-trade-meta" style="color:${(t.alpha_pct||0) >= 0 ? 'var(--success)' : 'var(--danger)'}">${(t.alpha_pct||0) >= 0 ? '+' : ''}${(t.alpha_pct||0).toFixed(1)}%</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Days</div>
                <div class="pt-trade-meta">${t.days_held||0}d</div>
            </div>
            <div>
                <div class="pt-trade-meta-label">Exit</div>
                <div style="font-size:10px;color:var(--text-muted)">${t.exit_reason||''}</div>
            </div>
        </div>`;
    }
}

function renderStats(stats) {
    const grid = document.getElementById('ptStats');
    const wr = stats.win_rate || 0;
    const wrClass = wr >= 50 ? 'positive' : wr > 0 ? 'negative' : '';
    const avgRet = stats.avg_return_pct || 0;
    const avgAlpha = stats.avg_alpha_pct || 0;

    grid.innerHTML = `
        <div class="pt-stat"><div class="pt-stat-label">Total</div><div class="pt-stat-value">${stats.total_trades||0}</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Win Rate</div><div class="pt-stat-value ${wrClass}">${wr.toFixed(1)}%</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Avg Return</div><div class="pt-stat-value ${avgRet >= 0 ? 'positive' : 'negative'}">${avgRet >= 0 ? '+' : ''}${avgRet.toFixed(1)}%</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Avg Alpha</div><div class="pt-stat-value ${avgAlpha >= 0 ? 'positive' : 'negative'}">${avgAlpha >= 0 ? '+' : ''}${avgAlpha.toFixed(1)}%</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Profit Factor</div><div class="pt-stat-value">${(stats.profit_factor||0).toFixed(2)}</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Expectancy</div><div class="pt-stat-value ${(stats.expectancy||0) >= 0 ? 'positive' : 'negative'}">${(stats.expectancy||0).toFixed(2)}%</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Avg Days</div><div class="pt-stat-value">${(stats.avg_days_held||0).toFixed(0)}</div></div>
        <div class="pt-stat"><div class="pt-stat-label">Max DD</div><div class="pt-stat-value negative">${(stats.max_drawdown_pct||0).toFixed(1)}%</div></div>
    `;
}

async function loadPaperTrades() {
    try {
        const [tradesResp, statsResp] = await Promise.all([
            fetch('/api/paper-trades'),
            fetch('/api/paper-trades/stats')
        ]);
        const tradesData = await tradesResp.json();
        const statsData = await statsResp.json();

        ptTrades = tradesData.trades || [];
        ptStats = statsData;

        document.getElementById('ptLoading').style.display = 'none';
        document.getElementById('ptContent').style.display = 'block';

        renderStats(ptStats);

        const openTrades = ptTrades.filter(t => t.status === 'OPEN');
        const closedTrades = ptTrades.filter(t => t.status === 'CLOSED');

        document.getElementById('ptOpen').innerHTML = openTrades.length
            ? openTrades.map(t => renderTradeRow(t, true)).join('')
            : '<div class="v3-empty-state" style="min-height:150px;padding:30px"><div class="v3-empty-title">No Open Trades</div><div class="v3-empty-desc">Paper trades are opened automatically during each scan cycle.</div></div>';

        document.getElementById('ptClosed').innerHTML = closedTrades.length
            ? closedTrades.map(t => renderTradeRow(t, false)).join('')
            : '<div class="v3-empty-state" style="min-height:150px;padding:30px"><div class="v3-empty-title">No Closed Trades</div><div class="v3-empty-desc">Closed trades will appear after positions hit targets, stops, or time limits.</div></div>';

        // Update tab labels
        document.querySelectorAll('#ptTabs .v3-tab')[0].textContent = `Open (${openTrades.length})`;
        document.querySelectorAll('#ptTabs .v3-tab')[1].textContent = `Closed (${closedTrades.length})`;

    } catch (e) {
        document.getElementById('ptLoading').innerHTML = '<div style="color:var(--danger);text-align:center;padding:30px">Failed to load paper trades</div>';
    }
}

loadPaperTrades();
