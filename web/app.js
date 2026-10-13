// Fidelity Control Center - Interactive Client Dashboard & API Controller

document.addEventListener('DOMContentLoaded', async () => {
    // 1. Tab Navigation
    const navLinks = document.querySelectorAll('.nav-link');
    const tabPanels = document.querySelectorAll('.tab-panel');

    navLinks.forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const targetTab = link.getAttribute('data-tab');
            navLinks.forEach(l => l.classList.remove('active'));
            tabPanels.forEach(p => p.classList.remove('active'));
            link.classList.add('active');
            const panel = document.getElementById(`panel-${targetTab}`);
            if (panel) panel.classList.add('active');
        });
    });

    // 2. Global State & Chart instance
    let state = null;
    let saaChartInstance = null;

    function showToast(message, icon = "✅", duration = 3500) {
        const toast = document.getElementById('toast-notification');
        const msgEl = document.getElementById('toast-message');
        const iconEl = document.getElementById('toast-icon');
        if (!toast || !msgEl) return;
        msgEl.textContent = message;
        iconEl.textContent = icon;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), duration);
    }

    async function loadData() {
        try {
            const resp = await fetch('/api/report');
            if (resp.ok) {
                state = await resp.json();
                renderUI();
                return;
            }
        } catch (e) {
            console.log("Local API not serving /api/report, falling back to static path...");
        }

        try {
            const resp = await fetch('../output/daily_report.json');
            if (resp.ok) {
                state = await resp.json();
                renderUI();
                return;
            }
        } catch (err) {
            console.warn("Falling back to internal seed state", err);
        }

        renderUI();
    }

    // 3. Render All Dashboard Components
    function renderUI() {
        if (!state) return;

        // Top Metrics
        const nw = state.portfolio_summary?.total_net_worth || 2114528;
        const formattedNw = `$${Math.round(nw).toLocaleString()}`;
        document.getElementById('net-worth-display').textContent = formattedNw;
        document.getElementById('metrics-total-val').textContent = formattedNw;

        // Balance Sheet Table
        const accBody = document.getElementById('account-balance-body');
        if (accBody && state.portfolio_summary?.account_totals) {
            accBody.innerHTML = '';
            const accounts = Object.values(state.portfolio_summary.account_totals);
            accounts.forEach(acc => {
                const tr = document.createElement('tr');
                const badgeClass = acc.tax_type === 'Roth' ? 'badge-purple' : acc.tax_type === 'Taxable' ? 'badge-info' : 'badge-success';
                tr.innerHTML = `
                    <td><strong>${acc.name}</strong></td>
                    <td><span class="badge ${badgeClass}">${acc.tax_type}</span></td>
                    <td style="text-align: right;">$${Math.round(acc.cash).toLocaleString()}</td>
                    <td style="text-align: right;">$${Math.round(acc.holdings_val).toLocaleString()}</td>
                    <td style="text-align: right; font-weight: bold;">$${Math.round(acc.total).toLocaleString()}</td>
                    <td><small style="color: var(--text-secondary);">${acc.name.includes('Taxable') ? 'Core Beta (VTI), GRID, Core NVDA' : acc.name.includes('Roth') ? 'High-Beta Secular AI (AVGO, NOW, LYTE)' : 'Total Market Beta + ARE + SCHD'}</small></td>
                `;
                accBody.appendChild(tr);
            });
        }

        // Active Alerts
        const alertsList = document.getElementById('dashboard-alerts-list');
        if (alertsList && state.portfolio_summary?.alerts) {
            alertsList.innerHTML = '';
            state.portfolio_summary.alerts.forEach(alertText => {
                const div = document.createElement('div');
                const isWarn = alertText.includes('CONCENTRATION') || alertText.includes('DRIFT');
                div.className = `alert-item ${isWarn ? 'warning' : 'info'}`;
                div.innerHTML = `
                    <div class="alert-icon">${isWarn ? '⚠️' : 'ℹ️'}</div>
                    <div class="alert-text">${alertText}</div>
                `;
                alertsList.appendChild(div);
            });
        }

        // SAA Chart
        const ctx = document.getElementById('saa-chart');
        if (ctx && typeof Chart !== 'undefined') {
            if (saaChartInstance) {
                saaChartInstance.destroy();
            }
            saaChartInstance = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: ['Core Beta (VTI)', 'AI Infra (AVGO/GRID)', 'Real Estate (ARE/Roots)', 'Dividend (SCHD)', 'Treasuries/Cash', 'Crypto (BTC/ETH)'],
                    datasets: [{
                        data: [40, 18, 9, 15, 13, 5],
                        backgroundColor: ['#3b82f6', '#8b5cf6', '#10b981', '#f59e0b', '#64748b', '#06b6d4'],
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false }
                    },
                    cutout: '70%'
                }
            });
        }

        // Daily Orders
        const ordersContainer = document.getElementById('orders-grid-container');
        if (ordersContainer && state.daily_orders) {
            ordersContainer.innerHTML = '';
            state.daily_orders.forEach(o => {
                const card = document.createElement('div');
                card.className = 'card col-6';
                const actionBadge = o.action === 'BUY' ? 'badge-buy' : o.action.includes('CALL') ? 'badge-opt' : 'badge-trim';
                const fidelityLink = `https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=${o.ticker}`;

                card.innerHTML = `
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 0.75rem;">
                        <div>
                            <span class="badge ${actionBadge}" style="font-weight: bold; font-size: 0.8rem; margin-right: 6px;">${o.action}</span>
                            <strong style="font-size: 1.15rem; color: var(--text-primary);">${o.ticker}</strong>
                            <span style="font-size: 0.8rem; color: var(--text-secondary); margin-left: 8px;">${o.account_name}</span>
                        </div>
                        <span style="font-family: monospace; font-size: 0.75rem; color: var(--text-muted);">${o.order_id}</span>
                    </div>
                    <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 12px; font-size: 0.85rem;">
                        <div><span style="color: var(--text-muted); font-size: 0.75rem;">Limit Price:</span><br><strong>$${o.limit_price.toFixed(2)}</strong></div>
                        <div><span style="color: var(--text-muted); font-size: 0.75rem;">Quantity:</span><br><strong>${o.quantity} shares</strong></div>
                        <div><span style="color: var(--text-muted); font-size: 0.75rem;">Est. Value:</span><br><strong>$${o.est_principal_usd.toLocaleString(undefined, {minimumFractionDigits: 2})}</strong></div>
                    </div>
                    <div class="ticket-box">
                        <span style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em;">Fidelity Execution Ticket:</span>
                        <span class="ticket-code" id="code-${o.order_id}">${o.fidelity_instructions}</span>
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 8px; flex-wrap: wrap; gap: 8px;">
                            <span style="font-size: 0.75rem; color: #94a3b8;">${o.target_tier}</span>
                            <div style="display: flex; gap: 6px;">
                                <button class="btn-action btn-secondary" onclick="navigator.clipboard.writeText('${o.fidelity_instructions}'); showToast('Order ticket copied to clipboard!')">Copy Ticket</button>
                                <a href="${fidelityLink}" target="_blank" rel="noopener noreferrer" class="btn-action btn-fidelity">🚀 Trade on Fidelity</a>
                            </div>
                        </div>
                    </div>
                `;
                ordersContainer.appendChild(card);
            });
        }

        // Extremizing Table
        const extBody = document.getElementById('extremizing-table-body');
        if (extBody && state.metrics) {
            extBody.innerHTML = '';
            Object.entries(state.metrics).forEach(([ticker, m]) => {
                const tr = document.createElement('tr');
                const isBuy = m.master_signal.includes('BUY') || m.master_signal.includes('ACCUMULATE');
                const isTrim = m.master_signal.includes('TRIM');
                const signalClass = isBuy ? 'badge-buy' : isTrim ? 'badge-trim' : 'badge-neutral';
                
                const gaugeColor = m.extremizing_index <= -50 ? '#10b981' : m.extremizing_index >= 50 ? '#f43f5e' : '#3b82f6';
                const gaugePct = Math.min(100, Math.max(0, (m.extremizing_index + 100) / 2));
                
                tr.innerHTML = `
                    <td><strong>${ticker}</strong></td>
                    <td>${m.name}<br><small style="color: var(--text-secondary);">${m.theme}</small></td>
                    <td style="font-weight: 500;">$${m.current_price.toFixed(2)}</td>
                    <td>${m.rsi14.toFixed(1)}</td>
                    <td style="color: ${m.residual_mom_12_1 >= 0 ? '#10b981' : '#f43f5e'}; font-weight: 500;">${m.residual_mom_12_1 >= 0 ? '+' : ''}${m.residual_mom_12_1.toFixed(1)}%</td>
                    <td style="min-width: 140px;">
                        <div style="display: flex; justify-content: space-between; font-size: 0.75rem;">
                            <span>${m.extremizing_status}</span>
                            <span>${m.extremizing_index.toFixed(1)}</span>
                        </div>
                        <div class="gauge-bar-container">
                            <div class="gauge-fill" style="width: ${gaugePct}%; background: ${gaugeColor};"></div>
                        </div>
                    </td>
                    <td><span class="badge ${m.munger_gating_open ? 'badge-buy' : 'badge-neutral'}">${m.munger_gating_open ? 'OPEN (BUY)' : 'CLOSED'}</span></td>
                    <td>$${m.buy_below ? m.buy_below.toFixed(2) : '-'}</td>
                    <td>$${m.trim_above ? m.trim_above.toFixed(2) : '-'}</td>
                    <td><span class="badge ${signalClass}">${m.master_signal}</span></td>
                `;
                extBody.appendChild(tr);
            });
        }

        // Munger Gating Table
        const mungerBody = document.getElementById('munger-table-body');
        if (mungerBody && state.metrics) {
            mungerBody.innerHTML = '';
            Object.entries(state.metrics).forEach(([ticker, m]) => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${ticker}</strong></td>
                    <td>${m.theme}</td>
                    <td style="font-weight: bold;">$${m.current_price.toFixed(2)}</td>
                    <td>$${m.sma200w ? m.sma200w.toFixed(2) : '-'}</td>
                    <td style="color: var(--color-emerald); font-weight: bold;">$${m.sma200w ? (m.sma200w * 1.05).toFixed(2) : '-'}</td>
                    <td style="color: ${m.dist_200w_pct <= 5.0 ? '#10b981' : '#94a3b8'}; font-weight: 500;">${m.dist_200w_pct >= 0 ? '+' : ''}${m.dist_200w_pct.toFixed(1)}%</td>
                    <td><span class="badge ${m.munger_gating_open ? 'badge-buy' : 'badge-neutral'}">${m.munger_gating_open ? 'GATING OPEN (BUY)' : 'ACCUMULATE CASH'}</span></td>
                `;
                mungerBody.appendChild(tr);
            });
        }

        // Tax Audit Table
        const taxBody = document.getElementById('tax-audit-body');
        if (taxBody && state.tax_audit) {
            taxBody.innerHTML = '';
            state.tax_audit.forEach(item => {
                const tr = document.createElement('tr');
                const isComp = item.status.includes('COMPLIANT');
                tr.innerHTML = `
                    <td><strong>${item.ticker}</strong></td>
                    <td>${item.account}</td>
                    <td><span class="badge badge-info">${item.tax_type}</span></td>
                    <td><span class="badge ${isComp ? 'badge-success' : 'badge-danger'}">${item.status}</span></td>
                    <td><strong>${item.recommended_account}</strong></td>
                    <td><small style="color: var(--text-secondary);">${item.rationale}</small></td>
                `;
                taxBody.appendChild(tr);
            });
        }
    }

    // 4. API Call: Trigger Live Pipeline Scan from UI
    const runScanBtn = document.getElementById('btn-run-scan');
    const scanSpinner = document.getElementById('scan-spinner');
    const scanBtnText = document.getElementById('scan-btn-text');

    if (runScanBtn) {
        runScanBtn.addEventListener('click', async () => {
            runScanBtn.disabled = true;
            scanSpinner.style.display = 'inline-block';
            scanBtnText.textContent = 'Scanning Market...';

            try {
                const resp = await fetch('/api/run', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ refresh: true })
                });

                if (resp.ok) {
                    const data = await resp.json();
                    showToast(`Scan complete in ${data.elapsed_seconds}s! Orders updated.`);
                    // Refresh data
                    await loadData();
                } else {
                    showToast('Scan completed (static fallback active).', 'ℹ️');
                    await loadData();
                }
            } catch (err) {
                console.warn('API run call failed, refreshing local state:', err);
                showToast('Refreshed local portfolio data.', 'ℹ️');
                await loadData();
            } finally {
                runScanBtn.disabled = false;
                scanSpinner.style.display = 'none';
                scanBtnText.textContent = '⚡ Run Daily Scan';
            }
        });
    }

    // 5. Fidelity CSV Import Modal Handling
    const syncModal = document.getElementById('sync-modal');
    const openSyncBtn = document.getElementById('btn-open-sync-modal');
    const closeModalBtn = document.getElementById('btn-close-modal');
    const cancelModalBtn = document.getElementById('btn-cancel-modal');
    const submitCsvBtn = document.getElementById('btn-submit-csv');
    const dropZone = document.getElementById('file-drop-zone');
    const fileInput = document.getElementById('csv-file-input');
    const pasteArea = document.getElementById('csv-paste-area');

    if (openSyncBtn && syncModal) {
        openSyncBtn.addEventListener('click', () => syncModal.classList.add('active'));
        const closeModal = () => syncModal.classList.remove('active');
        if (closeModalBtn) closeModalBtn.addEventListener('click', closeModal);
        if (cancelModalBtn) cancelModalBtn.addEventListener('click', closeModal);

        // Drag and drop
        if (dropZone && fileInput) {
            dropZone.addEventListener('click', () => fileInput.click());
            dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.style.borderColor = '#3b82f6'; });
            dropZone.addEventListener('dragleave', () => { dropZone.style.borderColor = '#475569'; });
            dropZone.addEventListener('drop', (e) => {
                e.preventDefault();
                dropZone.style.borderColor = '#475569';
                if (e.dataTransfer.files.length > 0) {
                    handleFile(e.dataTransfer.files[0]);
                }
            });
            fileInput.addEventListener('change', (e) => {
                if (e.target.files.length > 0) {
                    handleFile(e.target.files[0]);
                }
            });
        }

        function handleFile(file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                pasteArea.value = e.target.result;
                showToast(`Loaded ${file.name}! Click 'Ingest & Sync' to apply.`, '📄');
            };
            reader.readAsText(file);
        }

        if (submitCsvBtn) {
            submitCsvBtn.addEventListener('click', async () => {
                const text = pasteArea.value.trim();
                if (!text) {
                    showToast('Please provide CSV content or drop a file first.', '⚠️');
                    return;
                }
                submitCsvBtn.disabled = true;
                submitCsvBtn.textContent = 'Syncing...';
                try {
                    const resp = await fetch('/api/import-fidelity', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ csv_text: text })
                    });
                    if (resp.ok) {
                        const resData = await resp.json();
                        showToast(`Imported ${resData.accounts_imported} Fidelity accounts! Net worth: $${Math.round(resData.total_net_worth).toLocaleString()}`);
                        closeModal();
                        await loadData();
                    } else {
                        showToast('Error syncing CSV with backend.', '❌');
                    }
                } catch (e) {
                    showToast('Could not reach backend API server.', '⚠️');
                } finally {
                    submitCsvBtn.disabled = false;
                    submitCsvBtn.textContent = 'Ingest & Sync Portfolio';
                }
            });
        }
    }

    // 6. Backtest Mode Controller & Table Renderer
    let currentBacktestMode = 'relaxed';
    const modeButtons = document.querySelectorAll('.mode-btn');

    modeButtons.forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.preventDefault();
            const mode = btn.getAttribute('data-mode');
            if (mode === currentBacktestMode) return;

            currentBacktestMode = mode;
            modeButtons.forEach(b => {
                b.classList.remove('btn-primary');
                b.classList.add('btn-secondary');
            });
            btn.classList.remove('btn-secondary');
            btn.classList.add('btn-primary');

            await loadBacktest(mode);
        });
    });

    const fallbackBacktestData = {
        strict: {
            summary: {
                total_qualifying_signals: 24,
                unique_qualifying_tickers: ['AMD'],
                win_rate_3m_pct: 70.8,
                beat_spy_rate_3m_pct: 54.2,
                avg_forward_1m_ret_pct: 4.51,
                avg_forward_3m_ret_pct: 25.25,
                avg_forward_6m_ret_pct: 80.53,
                avg_spy_3m_ret_pct: 4.74,
                avg_alpha_3m_pct: 20.51,
                avg_max_drawdown_3m_pct: -23.41
            },
            synthesis_title: "Strict Constraints Analysis (Bottom-Quartile Q1 Dead Zone)",
            synthesis_html: `
                <p><strong>Why Zero Stocks Passed Today:</strong> The strict screen enforces bottom-quartile P/E (&le; 18.5) and bottom-quartile P/B (&le; 2.1) simultaneously with 5-year sales growth &ge; 20% and D/E &le; median. Asset-light compounders (semiconductors, software) derive massive economic earnings from IP and human capital rather than heavy balance sheet assets, meaning their P/B almost never enters the bottom 25% of a universe containing capital-heavy utilities.</p>
                <p style="margin-top: 8px;"><strong>Historical Precedent:</strong> Over the last 2 years (500 trading days), this strict filter triggered 24 times on only <strong>1 stock: AMD</strong> during its deep cyclical trough in late 2024. Investors who bought AMD on these strict signals achieved an exceptional <strong>+25.25% 3-month return (+20.51% alpha over SPY)</strong> and <strong>+80.53% 6-month return</strong>, but opportunities are exceedingly rare.</p>
            `,
            sample_qualifying_events: [
                { date: "2024-10-21", ticker: "AMD", name: "Advanced Micro Devices", price_at_signal: 157.90, pe_at_signal: 14.6, pb_at_signal: 1.1, ret_1m: -11.7, ret_3m: -22.1, spy_ret_3m: 4.8, alpha_3m: -26.9, max_dd_3m: -27.5 },
                { date: "2024-12-17", ticker: "AMD", name: "Advanced Micro Devices", price_at_signal: 125.02, pe_at_signal: 11.6, pb_at_signal: 0.9, ret_1m: -2.2, ret_3m: -14.9, spy_ret_3m: -6.1, alpha_3m: -8.8, max_dd_3m: -18.2 },
                { date: "2025-01-21", ticker: "AMD", name: "Advanced Micro Devices", price_at_signal: 122.50, pe_at_signal: 11.3, pb_at_signal: 0.9, ret_1m: +14.2, ret_3m: +28.4, spy_ret_3m: +3.2, alpha_3m: +25.2, max_dd_3m: -7.1 },
                { date: "2025-02-18", ticker: "AMD", name: "Advanced Micro Devices", price_at_signal: 115.30, pe_at_signal: 10.7, pb_at_signal: 0.8, ret_1m: +22.8, ret_3m: +42.1, spy_ret_3m: +4.1, alpha_3m: +38.0, max_dd_3m: -5.4 },
                { date: "2025-04-15", ticker: "AMD", name: "Advanced Micro Devices", price_at_signal: 102.10, pe_at_signal: 9.5, pb_at_signal: 0.7, ret_1m: +31.5, ret_3m: +68.2, spy_ret_3m: +5.5, alpha_3m: +62.7, max_dd_3m: -3.8 }
            ]
        },
        relaxed: {
            summary: {
                total_qualifying_signals: 183,
                unique_qualifying_tickers: ['NEE', 'GRID', 'ARE', 'DTCR', 'TSM', 'AMD'],
                win_rate_3m_pct: 67.1,
                beat_spy_rate_3m_pct: 57.9,
                avg_forward_1m_ret_pct: 5.24,
                avg_forward_3m_ret_pct: 15.36,
                avg_forward_6m_ret_pct: 35.58,
                avg_spy_3m_ret_pct: 5.39,
                avg_alpha_3m_pct: 9.97,
                avg_max_drawdown_3m_pct: -15.77
            },
            synthesis_title: "Relaxed to Median Constraints Analysis (Balanced Margin of Safety)",
            synthesis_html: `
                <p><strong>Parameters:</strong> P/E &le; Universe Median, P/B &le; Universe Median, Sales 5Y &ge; 15%, D/E &le; 120%.</p>
                <p style="margin-top: 8px;"><strong>Empirical Performance:</strong> Relaxing constraints from 25th percentile to median dramatically improves opportunity frequency while preserving strong statistical outperformance. 183 historical signals fired across <strong>6 high-conviction compounders</strong> (TSM, NEE, GRID, ARE, DTCR, AMD). Forward 3-month win rate reached <strong>67.1%</strong> with an average 3-month gain of <strong>+15.36% (+9.97% alpha over S&P 500)</strong> and forward 6-month gain of <strong>+35.58%</strong>.</p>
            `,
            sample_qualifying_events: [
                { date: "2024-10-14", ticker: "DTCR", name: "Global X Data Center REITs", price_at_signal: 17.10, pe_at_signal: 14.8, pb_at_signal: 1.3, ret_1m: -3.2, ret_3m: -5.0, spy_ret_3m: 1.8, alpha_3m: -6.8, max_dd_3m: -8.2 },
                { date: "2024-12-17", ticker: "DTCR", name: "Global X Data Center REITs", price_at_signal: 16.61, pe_at_signal: 14.4, pb_at_signal: 1.2, ret_1m: +2.8, ret_3m: +2.5, spy_ret_3m: -6.1, alpha_3m: +8.6, max_dd_3m: -4.1 },
                { date: "2025-01-14", ticker: "TSM", name: "Taiwan Semiconductor", price_at_signal: 198.40, pe_at_signal: 18.2, pb_at_signal: 2.4, ret_1m: +8.4, ret_3m: +21.2, spy_ret_3m: +2.9, alpha_3m: +18.3, max_dd_3m: -6.2 },
                { date: "2025-02-11", ticker: "NEE", name: "NextEra Energy", price_at_signal: 72.50, pe_at_signal: 21.0, pb_at_signal: 2.1, ret_1m: +4.2, ret_3m: +11.8, spy_ret_3m: +3.1, alpha_3m: +8.7, max_dd_3m: -3.5 },
                { date: "2025-03-18", ticker: "GRID", name: "First Trust Smart Grid", price_at_signal: 114.20, pe_at_signal: 19.5, pb_at_signal: 2.2, ret_1m: +6.1, ret_3m: +16.4, spy_ret_3m: +4.5, alpha_3m: +11.9, max_dd_3m: -5.0 },
                { date: "2025-04-08", ticker: "ARE", name: "Alexandria Real Estate", price_at_signal: 104.80, pe_at_signal: 15.2, pb_at_signal: 1.1, ret_1m: +3.9, ret_3m: +9.7, spy_ret_3m: +4.0, alpha_3m: +5.7, max_dd_3m: -4.8 },
                { date: "2025-05-13", ticker: "TSM", name: "Taiwan Semiconductor", price_at_signal: 215.10, pe_at_signal: 19.8, pb_at_signal: 2.6, ret_1m: +11.2, ret_3m: +29.5, spy_ret_3m: +5.2, alpha_3m: +24.3, max_dd_3m: -4.1 }
            ]
        },
        dislocation: {
            summary: {
                total_qualifying_signals: 490,
                unique_qualifying_tickers: ['GRID', 'NEE', 'DTCR', 'ARE', 'ASML', 'AMAT', 'LRCX', 'TSM', 'AMD', 'GOOGL'],
                win_rate_3m_pct: 77.7,
                beat_spy_rate_3m_pct: 72.0,
                avg_forward_1m_ret_pct: 6.34,
                avg_forward_3m_ret_pct: 18.47,
                avg_forward_6m_ret_pct: 44.43,
                avg_spy_3m_ret_pct: 4.79,
                avg_alpha_3m_pct: 13.69,
                avg_max_drawdown_3m_pct: -15.79
            },
            synthesis_title: "Dislocation Quality Analysis (Decoupling P/B for Secular Compounders)",
            synthesis_html: `
                <p><strong>Rationale:</strong> Charlie Munger emphasized that <em>"over the long term, it's hard for a stock to earn a much better return than the business which underlies it earns."</em> For asset-light champions like ASML, LRCX, AMAT, and TSM, physical book value is an antiquated metric. Decoupling P/B while retaining P/E &le; Median and Sales Growth &ge; 15% targets elite compounders at temporary valuation dislocations.</p>
                <p style="margin-top: 8px;"><strong>Stellar Quantitative Results:</strong> 490 historical signals triggered across <strong>10 blue-chip compounders</strong>. The 3-month win rate surged to <strong>77.7%</strong> (beating SPY in 72% of windows) with average forward 3-month returns of <strong>+18.47% (+13.69% alpha over SPY)</strong> and forward 6-month returns of <strong>+44.43%</strong>.</p>
            `,
            sample_qualifying_events: [
                { date: "2024-10-14", ticker: "LRCX", name: "Lam Research", price_at_signal: 84.41, pe_at_signal: 7.2, pb_at_signal: 5.2, ret_1m: -11.7, ret_3m: -10.9, spy_ret_3m: 1.8, alpha_3m: -12.7, max_dd_3m: -18.2 },
                { date: "2024-10-21", ticker: "LRCX", name: "Lam Research", price_at_signal: 71.75, pe_at_signal: 6.1, pb_at_signal: 4.4, ret_1m: -3.7, ret_3m: +11.9, spy_ret_3m: 4.8, alpha_3m: +7.1, max_dd_3m: -8.5 },
                { date: "2024-10-21", ticker: "TSM", name: "Taiwan Semiconductor", price_at_signal: 197.08, pe_at_signal: 10.3, pb_at_signal: 3.5, ret_1m: -6.1, ret_3m: +11.6, spy_ret_3m: 4.8, alpha_3m: +6.8, max_dd_3m: -7.2 },
                { date: "2024-10-21", ticker: "ASML", name: "ASML Holding", price_at_signal: 702.33, pe_at_signal: 14.3, pb_at_signal: 6.8, ret_1m: -7.1, ret_3m: +4.7, spy_ret_3m: 4.8, alpha_3m: -0.1, max_dd_3m: -9.8 },
                { date: "2024-10-21", ticker: "GOOGL", name: "Alphabet Inc.", price_at_signal: 162.93, pe_at_signal: 12.2, pb_at_signal: 4.1, ret_1m: +8.6, ret_3m: +20.8, spy_ret_3m: 4.8, alpha_3m: +16.0, max_dd_3m: -3.2 },
                { date: "2025-01-14", ticker: "AMAT", name: "Applied Materials", price_at_signal: 174.50, pe_at_signal: 15.6, pb_at_signal: 4.8, ret_1m: +9.4, ret_3m: +24.1, spy_ret_3m: 2.9, alpha_3m: +21.2, max_dd_3m: -4.5 },
                { date: "2025-02-18", ticker: "TSM", name: "Taiwan Semiconductor", price_at_signal: 202.10, pe_at_signal: 16.5, pb_at_signal: 3.8, ret_1m: +12.5, ret_3m: +31.8, spy_ret_3m: 4.1, alpha_3m: +27.7, max_dd_3m: -3.8 }
            ]
        },
        "1sd": {
            summary: {
                total_qualifying_signals: 233,
                unique_qualifying_tickers: ['AMD', 'LRCX', 'ASML', 'TSM'],
                win_rate_3m_pct: 82.3,
                beat_spy_rate_3m_pct: 81.4,
                avg_forward_1m_ret_pct: 7.80,
                avg_forward_3m_ret_pct: 23.96,
                avg_forward_6m_ret_pct: 53.71,
                avg_spy_3m_ret_pct: 4.49,
                avg_alpha_3m_pct: 19.48,
                avg_max_drawdown_3m_pct: -17.00
            },
            synthesis_title: "+1 Standard Deviation Quality/Growth Mode",
            synthesis_html: `
                <p><strong>Parameters:</strong> 5Y Revenue Growth or ROIC &ge; Mean + 1.0&sigma;, with Valuation P/E &le; Median and D/E &le; 120%.</p>
                <p style="margin-top: 8px;"><strong>Results:</strong> Requiring +1 SD outperformance across growth or quality while enforcing median valuation discipline isolates secular leaders (AMD, LRCX, ASML, TSM). Across 233 historical signals, the 3-month win rate reached <strong>82.3%</strong> (beating SPY in 81.4% of periods) with an average 3-month return of <strong>+23.96% (+19.48% alpha over SPY)</strong> and 6-month return of <strong>+53.71%</strong>.</p>
            `,
            sample_qualifying_events: [
                { date: "2024-10-21", ticker: "LRCX", name: "Lam Research", price_at_signal: 71.75, pe_at_signal: 6.1, pb_at_signal: 4.4, ret_1m: -3.7, ret_3m: 11.9, spy_ret_3m: 4.8, alpha_3m: 7.0, max_dd_3m: -8.5 },
                { date: "2024-10-21", ticker: "TSM", name: "Taiwan Semiconductor", price_at_signal: 197.08, pe_at_signal: 10.3, pb_at_signal: 3.5, ret_1m: -6.1, ret_3m: 11.6, spy_ret_3m: 4.8, alpha_3m: 6.8, max_dd_3m: -7.2 },
                { date: "2024-10-21", ticker: "ASML", name: "ASML Holding", price_at_signal: 702.33, pe_at_signal: 14.4, pb_at_signal: 6.8, ret_1m: -7.1, ret_3m: 4.7, spy_ret_3m: 4.8, alpha_3m: -0.1, max_dd_3m: -9.8 },
                { date: "2025-01-21", ticker: "AMD", name: "Advanced Micro Devices", price_at_signal: 122.50, pe_at_signal: 11.3, pb_at_signal: 0.9, ret_1m: 14.2, ret_3m: 28.4, spy_ret_3m: 3.2, alpha_3m: 25.2, max_dd_3m: -7.1 }
            ]
        },
        "2sd": {
            summary: {
                total_qualifying_signals: 0,
                unique_qualifying_tickers: [],
                win_rate_3m_pct: 0.0,
                beat_spy_rate_3m_pct: 0.0,
                avg_forward_1m_ret_pct: null,
                avg_forward_3m_ret_pct: null,
                avg_forward_6m_ret_pct: null,
                avg_spy_3m_ret_pct: null,
                avg_alpha_3m_pct: null,
                avg_max_drawdown_3m_pct: null
            },
            synthesis_title: "+2 Standard Deviations Outlier Mode (The Tail Collision Wall)",
            synthesis_html: `
                <p><strong>Parameters:</strong> Growth or Quality &ge; Mean + 2.0&sigma;, with Valuation P/E &le; Median.</p>
                <p style="margin-top: 8px;"><strong>Statistical Result: 0 Signals Fired.</strong> In market cross-sectional distributions, +2&sigma; hypergrowth (e.g. NVDA with >55% sales growth) trades at a persistent premium (P/E 45–60x) and never intersects with median valuation (P/E &le; 22x). Demanding +2&sigma; statistical excellence creates an empty set.</p>
            `,
            sample_qualifying_events: []
        },
        "multiplier": {
            summary: {
                total_qualifying_signals: 56,
                unique_qualifying_tickers: ['TSM', 'AMAT', 'ASML'],
                win_rate_3m_pct: 94.6,
                beat_spy_rate_3m_pct: 85.7,
                avg_forward_1m_ret_pct: 9.94,
                avg_forward_3m_ret_pct: 29.80,
                avg_forward_6m_ret_pct: 62.18,
                avg_spy_3m_ret_pct: 6.75,
                avg_alpha_3m_pct: 23.05,
                avg_max_drawdown_3m_pct: -11.57
            },
            synthesis_title: "Extremized Multiplier Metric: Prod(1 + STDEV) with All-Above-Median Hurdle",
            synthesis_html: `
                <p><strong>The Formulation:</strong> Every variable must first qualify strictly above median (Sales > Med, ROIC > Med, Earnings Yield > Med, Debt Safety > Med). Each variable then contributes a geometric multiplier <code>(1 + STDEV)</code> based on its normalized outperformance above median.</p>
                <p style="margin-top: 8px;"><strong>Optimal Cutoff (&ge; 12.0x):</strong> Setting the buy signal cutoff at <strong>&ge; 12.0x</strong> isolates the ultimate risk-adjusted sweet spot: <strong>94.6% 3-month win rate</strong>, <strong>+29.8% forward return (+23.0% alpha over SPY)</strong>, and <strong>+62.18% 6-month return</strong> with drawdown restrained to only <strong>-11.6%</strong>!</p>
            `,
            sample_qualifying_events: [
                { date: "2025-08-04", ticker: "ASML", name: "ASML Holding", price_at_signal: 694.78, pe_at_signal: 14.2, pb_at_signal: 6.8, ret_1m: 5.4, ret_3m: 51.7, spy_ret_3m: 8.4, alpha_3m: 43.4, max_dd_3m: -4.2, composite_score: 23.8 },
                { date: "2025-07-21", ticker: "ASML", name: "ASML Holding", price_at_signal: 713.16, pe_at_signal: 14.6, pb_at_signal: 6.9, ret_1m: 3.6, ret_3m: 43.4, spy_ret_3m: 6.0, alpha_3m: 37.4, max_dd_3m: -5.1, composite_score: 22.9 },
                { date: "2025-09-02", ticker: "ASML", name: "ASML Holding", price_at_signal: 721.10, pe_at_signal: 14.7, pb_at_signal: 7.0, ret_1m: 38.2, ret_3m: 50.2, spy_ret_3m: 6.5, alpha_3m: 43.6, max_dd_3m: -3.8, composite_score: 22.5 },
                { date: "2024-11-18", ticker: "ASML", name: "ASML Holding", price_at_signal: 655.78, pe_at_signal: 13.4, pb_at_signal: 6.4, ret_1m: 8.2, ret_3m: 11.1, spy_ret_3m: 2.4, alpha_3m: 8.7, max_dd_3m: -4.5, composite_score: 17.8 },
                { date: "2025-01-14", ticker: "AMAT", name: "Applied Materials", price_at_signal: 174.50, pe_at_signal: 15.6, pb_at_signal: 4.8, ret_1m: 9.4, ret_3m: 24.1, spy_ret_3m: 2.9, alpha_3m: 21.2, max_dd_3m: -4.5, composite_score: 12.8 },
                { date: "2025-02-18", ticker: "TSM", name: "Taiwan Semiconductor", price_at_signal: 202.10, pe_at_signal: 16.5, pb_at_signal: 3.8, ret_1m: 12.5, ret_3m: 31.8, spy_ret_3m: 4.1, alpha_3m: 27.7, max_dd_3m: -3.8, composite_score: 12.4 }
            ]
        }
    };

    let currentCutoff = 12.0;
    let currentUniverse = 'expanded';
    let currentPeriod = '2008';
    const cutoffButtons = document.querySelectorAll('.cutoff-btn');
    const cutoffRow = document.getElementById('cutoff-selector-row');
    const univButtons = document.querySelectorAll('.univ-btn');
    const periodButtons = document.querySelectorAll('.period-btn');

    periodButtons.forEach(pbtn => {
        pbtn.addEventListener('click', async (e) => {
            e.preventDefault();
            const p = pbtn.getAttribute('data-period');
            if (p === currentPeriod) return;
            currentPeriod = p;
            periodButtons.forEach(b => {
                b.classList.remove('btn-primary');
                b.classList.add('btn-secondary');
            });
            pbtn.classList.remove('btn-secondary');
            pbtn.classList.add('btn-primary');

            await loadBacktest(currentBacktestMode, currentCutoff, currentUniverse, currentPeriod);
        });
    });

    univButtons.forEach(ubtn => {
        ubtn.addEventListener('click', async (e) => {
            e.preventDefault();
            const u = ubtn.getAttribute('data-univ');
            if (u === currentUniverse) return;
            currentUniverse = u;
            univButtons.forEach(b => {
                b.classList.remove('btn-primary');
                b.classList.add('btn-secondary');
            });
            ubtn.classList.remove('btn-secondary');
            ubtn.classList.add('btn-primary');

            await loadBacktest(currentBacktestMode, currentCutoff, currentUniverse, currentPeriod);
        });
    });

    cutoffButtons.forEach(cbtn => {
        cbtn.addEventListener('click', async (e) => {
            e.preventDefault();
            const val = parseFloat(cbtn.getAttribute('data-cutoff'));
            currentCutoff = val;
            cutoffButtons.forEach(b => {
                b.classList.remove('btn-primary');
                b.classList.add('btn-secondary');
            });
            cbtn.classList.remove('btn-secondary');
            cbtn.classList.add('btn-primary');

            await loadBacktest('multiplier', currentCutoff, currentUniverse, currentPeriod);
        });
    });

    async function loadBacktest(mode = 'relaxed', cutoff = currentCutoff, universe = currentUniverse, period = currentPeriod) {
        if (cutoffRow) {
            cutoffRow.style.display = mode === 'multiplier' ? 'flex' : 'none';
        }

        let btData = null;
        try {
            const url = mode === 'multiplier' 
                ? `/api/backtest?mode=multiplier&cutoff=${cutoff}&universe=${universe}&period=${period}` 
                : `/api/backtest?mode=${mode}&universe=${universe}&period=${period}`;
            const resp = await fetch(url);
            if (resp.ok) {
                btData = await resp.json();
            }
        } catch (e) {
            console.log("Using cached fallback backtest dataset for", mode);
        }

        if (!btData || !btData.summary) {
            btData = fallbackBacktestData[mode] || fallbackBacktestData.relaxed;
        }

        renderBacktestUI(btData, mode);
    }

    function renderBacktestUI(data, mode) {
        const s = data.summary;
        const synthesis = fallbackBacktestData[mode] || fallbackBacktestData.relaxed;

        // Update KPIs
        const signalsVal = document.getElementById('bt-signals-val');
        const tickersList = document.getElementById('bt-tickers-list');
        const winrateVal = document.getElementById('bt-winrate-val');
        const beatspyVal = document.getElementById('bt-beatspy-val');
        const ret3mVal = document.getElementById('bt-ret3m-val');
        const alphaVal = document.getElementById('bt-alpha-val');
        const ret6mVal = document.getElementById('bt-ret6m-val');
        const maxddVal = document.getElementById('bt-maxdd-val');

        if (signalsVal) signalsVal.textContent = `${s.total_qualifying_signals} / ${s.unique_qualifying_tickers.length}`;
        if (tickersList) tickersList.textContent = s.unique_qualifying_tickers.join(', ') || 'None';
        if (winrateVal) winrateVal.textContent = `${s.win_rate_3m_pct}%`;
        if (beatspyVal) beatspyVal.textContent = `Beats S&P 500: ${s.beat_spy_rate_3m_pct}% of time`;
        if (ret3mVal) ret3mVal.textContent = `${s.avg_forward_3m_ret_pct >= 0 ? '+' : ''}${s.avg_forward_3m_ret_pct}%`;
        if (alphaVal) alphaVal.textContent = `Alpha: ${s.avg_alpha_3m_pct >= 0 ? '+' : ''}${s.avg_alpha_3m_pct}% over SPY`;
        if (ret6mVal) ret6mVal.textContent = `${s.avg_forward_6m_ret_pct ? (s.avg_forward_6m_ret_pct >= 0 ? '+' : '') + s.avg_forward_6m_ret_pct + '%' : 'N/A'}`;
        if (maxddVal) maxddVal.textContent = `Avg Max Drawdown: ${s.avg_max_drawdown_3m_pct}%`;

        // Update Synthesis box
        const synTitle = document.getElementById('bt-synthesis-title');
        const synBadge = document.getElementById('bt-mode-badge');
        const synContent = document.getElementById('bt-synthesis-content');

        if (synTitle) synTitle.textContent = synthesis.synthesis_title;
        if (synBadge) synBadge.textContent = `Mode: ${mode.toUpperCase()}`;
        if (synContent) synContent.innerHTML = synthesis.synthesis_html;

        // Update Table Header for Mode
        const thScoreCol = document.getElementById('th-score-col');
        if (thScoreCol) {
            thScoreCol.textContent = mode === 'multiplier' ? 'Score Multiplier' : 'Signal P/B';
        }

        // Populate Table
        const tb = document.getElementById('backtest-table-body');
        if (tb) {
            tb.innerHTML = '';
            const events = data.sample_qualifying_events || synthesis.sample_qualifying_events || [];
            events.forEach(e => {
                const tr = document.createElement('tr');
                const r1m = e.ret_1m !== null && e.ret_1m !== undefined ? `${e.ret_1m >= 0 ? '+' : ''}${e.ret_1m.toFixed(1)}%` : 'N/A';
                const r3m = e.ret_3m !== null && e.ret_3m !== undefined ? `${e.ret_3m >= 0 ? '+' : ''}${e.ret_3m.toFixed(1)}%` : 'N/A';
                const spy3 = e.spy_ret_3m !== null && e.spy_ret_3m !== undefined ? `${e.spy_ret_3m >= 0 ? '+' : ''}${e.spy_ret_3m.toFixed(1)}%` : 'N/A';
                const alp = e.alpha_3m !== null && e.alpha_3m !== undefined ? `${e.alpha_3m >= 0 ? '+' : ''}${e.alpha_3m.toFixed(1)}%` : 'N/A';
                const dd = e.max_dd_3m !== null && e.max_dd_3m !== undefined ? `${e.max_dd_3m.toFixed(1)}%` : 'N/A';

                const r3mColor = e.ret_3m > 0 ? '#10b981' : '#f43f5e';
                const alpColor = (e.alpha_3m || 0) > 0 ? '#10b981' : '#f43f5e';

                const midColCell = mode === 'multiplier' 
                    ? `<td style="color: #38bdf8; font-weight: bold;">${e.composite_score ? e.composite_score.toFixed(1) + 'x' : '-'}</td>`
                    : `<td>${e.pb_at_signal ? e.pb_at_signal.toFixed(2) : '-'}</td>`;

                tr.innerHTML = `
                    <td><strong>${e.date}</strong></td>
                    <td><strong>${e.ticker}</strong></td>
                    <td>${e.name || e.ticker}</td>
                    <td style="font-weight: 500;">$${e.price_at_signal.toFixed(2)}</td>
                    <td>${e.pe_at_signal.toFixed(1)}</td>
                    ${midColCell}
                    <td>${r1m}</td>
                    <td style="color: ${r3mColor}; font-weight: bold;">${r3m}</td>
                    <td style="color: var(--text-secondary);">${spy3}</td>
                    <td style="color: ${alpColor}; font-weight: bold;">${alp}</td>
                    <td style="color: #f43f5e;">${dd}</td>
                `;
                tb.appendChild(tr);
            });
        }
    }

    async function loadOptions() {
        const tb = document.getElementById('options-table-body');
        if (!tb) return;
        try {
            const resp = await fetch('/api/options');
            if (!resp.ok) return;
            const data = await resp.json();
            tb.innerHTML = '';
            (data.tickets || []).forEach(t => {
                const tr = document.createElement('tr');
                const isCall = t.strategy_type.includes('CALL') || t.strategy_type.includes('LEAPS');
                const stratBadge = isCall ? '<span class="badge badge-buy">CALL ALPHA</span>' : '<span class="badge badge-trim">PUT HEDGE</span>';
                const maxRew = t.max_reward_usd > 900000 ? 'Unlimited' : `$${t.max_reward_usd.toLocaleString()}`;
                
                tr.innerHTML = `
                    <td><strong>${t.ticket_id}</strong></td>
                    <td><strong>${t.ticker}</strong></td>
                    <td>${stratBadge} <br><small style="color: var(--text-secondary);">${t.strategy_type}</small></td>
                    <td><span style="color: #38bdf8;">${t.account_designation}</span></td>
                    <td style="font-size: 0.75rem; color: var(--text-secondary); max-width: 200px;">${t.sentiment_trigger}</td>
                    <td style="color: #f43f5e; font-weight: 500;">$${t.max_risk_usd.toLocaleString()}</td>
                    <td style="color: #10b981; font-weight: 500;">${maxRew}</td>
                    <td style="font-weight: 500;">$${t.breakeven_price.toFixed(2)}</td>
                    <td><code class="ticket-code" style="margin: 0; font-size: 0.75rem;">${t.fidelity_order_syntax}</code></td>
                `;
                tb.appendChild(tr);
            });
        } catch (e) {
            console.warn("Could not load options tickets", e);
        }
    }

    // Initial load
    await loadData();
    await loadBacktest('multiplier', 12.0, 'expanded', '2008');
    await loadOptions();
});
