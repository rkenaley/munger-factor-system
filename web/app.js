// ==============================================================================
// MUNGER FACTOR TERMINAL - CLIENT-SIDE REACTIVE CONTROLLER (v2.2)
// ==============================================================================

document.addEventListener('DOMContentLoaded', () => {
    // --------------------------------------------------------------------------
    // 1. GLOBAL STATE
    // --------------------------------------------------------------------------
    const state = {
        currentStep: 1,
        activeFilter: 'all',
        insiderFilterActive: false,
        searchQuery: '',
        sortColumn: 'conviction_m',
        sortAsc: false,
        candidates: [],
        selectedBuys: new Set(),
        lots: [],
        collars: [],
        spreads: [],
        overlay8020Active: true,
        activeOptionsTab: 'spreads',
        radarChart: null,
        isScanning: false,
        status: null,
    };

    // --------------------------------------------------------------------------
    // 2. TOAST NOTIFICATION UTILITY
    // --------------------------------------------------------------------------
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

    // --------------------------------------------------------------------------
    // 3. WIZARD STEPPER & TAB NAVIGATION
    // --------------------------------------------------------------------------
    function goToStep(stepNumber) {
        stepNumber = parseInt(stepNumber);
        if (stepNumber < 1 || stepNumber > 7) return;
        state.currentStep = stepNumber;

        // Update Stepper UI
        document.querySelectorAll('.wizard-step').forEach(btn => {
            const step = parseInt(btn.getAttribute('data-step'));
            btn.classList.remove('active');
            if (step === stepNumber) {
                btn.classList.add('active');
            } else if (step < stepNumber) {
                btn.classList.add('completed');
            } else {
                btn.classList.remove('completed');
            }
        });

        // Update Panels
        document.querySelectorAll('.stage-panel').forEach(p => p.classList.remove('active'));
        const activePanel = document.getElementById(`stage-${stepNumber}`);
        if (activePanel) activePanel.classList.add('active');

        // Update Quick Jump Tabs
        document.querySelectorAll('.app-tab-btn').forEach(tab => {
            const target = parseInt(tab.getAttribute('data-target-step'));
            if (target === stepNumber) {
                tab.classList.add('active');
            } else {
                tab.classList.remove('active');
            }
        });

        // Smooth scroll to top
        window.scrollTo({ top: 0, behavior: 'smooth' });

        // Trigger context-specific logic
        if (stepNumber === 4 && state.candidates.length === 0) {
            triggerScan();
        } else if (stepNumber === 5) {
            pollWatcherStatus();
            if (state.lots.length === 0) {
                loadFidelityReconciliation();
            }
        } else if (stepNumber === 6) {
            generateOrders();
        } else if (stepNumber === 7) {
            if (state.activeOptionsTab === 'spreads') {
                loadOptionsSpreads();
            } else {
                loadOptionsCollars();
            }
        }
    }

    // Stepper click handlers
    document.querySelectorAll('.wizard-step').forEach(btn => {
        btn.addEventListener('click', () => {
            goToStep(btn.getAttribute('data-step'));
        });
    });

    document.querySelectorAll('.btn-nav-step').forEach(btn => {
        btn.addEventListener('click', () => {
            const next = btn.getAttribute('data-next-step');
            if (next) goToStep(next);
        });
    });

    document.querySelectorAll('.app-tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const target = btn.getAttribute('data-target-step');
            if (target) goToStep(target);
        });
    });

    // --------------------------------------------------------------------------
    // 4. SLIDERS & LIVE READOUT BINDINGS
    // --------------------------------------------------------------------------
    const sliders = [
        { id: 'slider-min-munger', badgeId: 'val-min-munger', suffix: 'x', decimals: 1 },
        { id: 'slider-min-sales5y', badgeId: 'val-min-sales5y', suffix: '%', decimals: 1 },
        { id: 'slider-min-roic', badgeId: 'val-min-roic', suffix: '%', decimals: 1 },
        { id: 'slider-max-pe', badgeId: 'val-max-pe', suffix: 'x', decimals: 1 },
        { id: 'slider-max-de', badgeId: 'val-max-de', suffix: 'x', decimals: 1 },
        { id: 'slider-min-insider', badgeId: 'val-min-insider', prefix: '$', decimals: 0, isCurrency: true },
        { id: 'slider-max-alloc', badgeId: 'val-max-alloc', suffix: '%', decimals: 1 },
        { id: 'slider-cash-buffer', badgeId: 'val-cash-buffer', suffix: '%', decimals: 1 },
    ];

    sliders.forEach(s => {
        const input = document.getElementById(s.id);
        const badge = document.getElementById(s.badgeId);
        if (!input || !badge) return;

        input.addEventListener('input', () => {
            let val = parseFloat(input.value);
            if (s.isCurrency) {
                badge.textContent = `$${val.toLocaleString()}`;
            } else {
                badge.textContent = `${val.toFixed(s.decimals)}${s.suffix || ''}`;
            }
        });
    });

    // Checkbox toggles card styling
    ['chk-conjunctive', 'chk-value-trap', 'chk-gating200w', 'chk-cluster-boost', 'chk-csuite-haircut'].forEach(id => {
        const chk = document.getElementById(id);
        const card = document.getElementById(`${id}-card`) || (chk ? chk.closest('.toggle-card') : null);
        if (chk && card) {
            chk.addEventListener('change', () => {
                if (chk.checked) card.classList.add('active');
                else card.classList.remove('active');
            });
        }
    });

    // Universe radio card styling & auto-rescan
    document.querySelectorAll('input[name="universe-choice"]').forEach(radio => {
        radio.addEventListener('change', () => {
            document.querySelectorAll('input[name="universe-choice"]').forEach(r => {
                const card = r.closest('.toggle-card');
                if (card) {
                    if (r.checked) card.classList.add('active');
                    else card.classList.remove('active');
                }
            });
            triggerScan();
        });
    });

    // Strategy Presets
    const presets = {
        'munger-strict': {
            min_munger: 2.0, min_sales5y: 10, min_roic: 15, max_pe: 35, max_de: 2.5,
            conjunctive: true, value_trap: true, gating200w: false
        },
        'deep-value': {
            min_munger: 1.5, min_sales5y: 5, min_roic: 12, max_pe: 22, max_de: 1.5,
            conjunctive: true, value_trap: true, gating200w: false
        },
        'growth-compounder': {
            min_munger: 3.0, min_sales5y: 18, min_roic: 25, max_pe: 45, max_de: 3.0,
            conjunctive: true, value_trap: true, gating200w: false
        },
        'relaxed-screen': {
            min_munger: 1.0, min_sales5y: 0, min_roic: 10, max_pe: 55, max_de: 4.5,
            conjunctive: false, value_trap: true, gating200w: false
        }
    };

    document.querySelectorAll('.btn-preset').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.btn-preset').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const pKey = btn.getAttribute('data-preset');
            const p = presets[pKey];
            if (!p) return;

            document.getElementById('slider-min-munger').value = p.min_munger;
            document.getElementById('val-min-munger').textContent = `${p.min_munger.toFixed(1)}x`;

            document.getElementById('slider-min-sales5y').value = p.min_sales5y;
            document.getElementById('val-min-sales5y').textContent = `${p.min_sales5y.toFixed(1)}%`;

            document.getElementById('slider-min-roic').value = p.min_roic;
            document.getElementById('val-min-roic').textContent = `${p.min_roic.toFixed(1)}%`;

            document.getElementById('slider-max-pe').value = p.max_pe;
            document.getElementById('val-max-pe').textContent = `${p.max_pe.toFixed(1)}x`;

            document.getElementById('slider-max-de').value = p.max_de;
            document.getElementById('val-max-de').textContent = `${p.max_de.toFixed(1)}x`;

            const setChk = (id, val) => {
                const el = document.getElementById(id);
                if (el) {
                    el.checked = val;
                    const card = el.closest('.toggle-card');
                    if (card) {
                        if (val) card.classList.add('active');
                        else card.classList.remove('active');
                    }
                }
            };

            setChk('chk-conjunctive', p.conjunctive);
            setChk('chk-value-trap', p.value_trap);
            setChk('chk-gating200w', p.gating200w);

            showToast(`Applied preset: ${btn.textContent.trim().split(' ')[1]}`, "⚙️");
        });
    });

    // --------------------------------------------------------------------------
    // 5. STATUS & REFRESH APIS
    // --------------------------------------------------------------------------
    async function fetchStatus() {
        try {
            const resp = await fetch('/api/munger/status');
            if (resp.ok) {
                const data = await resp.json();
                state.status = data;
                updateStatusUI(data);
            }
        } catch (e) {
            console.warn("Could not reach /api/munger/status. Local fallback active.");
            updateStatusUI({
                is_stale: false,
                lag_hours: 0.2,
                latest_bar_date: "2026-10-14",
                session_state: "REGULAR_MARKET_OPEN",
                total_constituents: 176
            });
        }
    }

    function updateStatusUI(s) {
        const clockPill = document.getElementById('market-clock-pill');
        const clockText = document.getElementById('market-clock-text');
        const stalePill = document.getElementById('staleness-pill');
        const staleText = document.getElementById('staleness-text');

        if (clockText) {
            if (s.session_state === 'REGULAR_MARKET_OPEN') {
                clockText.textContent = "NYSE ACTIVE";
                clockPill.className = "status-pill open";
            } else if (s.session_state === 'WEEKEND_CLOSED') {
                clockText.textContent = "WEEKEND CLOSED";
                clockPill.className = "status-pill";
            } else {
                clockText.textContent = "AFTER-HOURS";
                clockPill.className = "status-pill";
            }
        }

        if (stalePill && staleText) {
            if (s.is_stale) {
                stalePill.className = "status-pill stale";
                staleText.textContent = `STALE (${s.lag_hours}h lag)`;
            } else {
                stalePill.className = "status-pill fresh";
                staleText.textContent = `DATA FRESH (${s.lag_hours}h lag)`;
            }
        }

        const dBar = document.getElementById('diag-bar-date');
        const dLag = document.getElementById('diag-lag-hours');
        const dSess = document.getElementById('diag-session-state');
        const dConst = document.getElementById('diag-constituents');

        if (dBar) dBar.textContent = s.latest_bar_date || '2026-10-14';
        if (dLag) dLag.textContent = `${s.lag_hours || 0.0} Hours`;
        if (dSess) dSess.textContent = s.session_state || 'ACTIVE';
        if (dConst) dConst.textContent = `${s.total_constituents || 176} Equities`;
    }

    // Refresh Prices
    async function triggerPriceRefresh() {
        const btn = document.getElementById('btn-refresh-prices');
        const spinner = document.getElementById('refresh-spinner');
        const txt = document.getElementById('refresh-btn-text');

        if (spinner) spinner.style.display = 'inline-block';
        if (txt) txt.textContent = 'Updating...';

        try {
            const resp = await fetch('/api/munger/refresh-prices', { method: 'POST' });
            if (resp.ok) {
                const res = await resp.json();
                showToast(`Price cache refreshed in ${res.elapsed_seconds}s! Latest: ${res.latest_bar_date}`, "⚡", 5000);
                fetchStatus();
                if (state.candidates.length > 0) triggerScan();
            } else {
                const errData = await resp.json().catch(() => ({}));
                showToast(`Server refresh error: ${errData.message || resp.statusText}`, "⚠️", 5000);
            }
        } catch (e) {
            if (window.location.protocol === 'file:') {
                showToast("Running in local file mode. Run '.\\start_server.ps1' in PowerShell to connect to the live price downloader!", "ℹ️", 6500);
            } else {
                showToast("Unable to reach backend server on port 8505. Ensure '.\\start_server.ps1' is running.", "⚠️", 5000);
            }
        } finally {
            if (spinner) spinner.style.display = 'none';
            if (txt) txt.textContent = '⚡ Refresh Prices';
        }
    }

    const btnRef1 = document.getElementById('btn-refresh-prices');
    if (btnRef1) btnRef1.addEventListener('click', triggerPriceRefresh);
    const btnRef2 = document.getElementById('btn-diag-refresh');
    if (btnRef2) btnRef2.addEventListener('click', triggerPriceRefresh);

    // --------------------------------------------------------------------------
    // 6. LIVE MUNGER SCANNER API
    // --------------------------------------------------------------------------
    function gatherScanParameters() {
        const selectedUniv = document.querySelector('input[name="universe-choice"]:checked')?.value || 'combined';
        return {
            universe: selectedUniv,
            min_munger: parseFloat(document.getElementById('slider-min-munger').value),
            min_sales5y: parseFloat(document.getElementById('slider-min-sales5y').value),
            min_roic: parseFloat(document.getElementById('slider-min-roic').value),
            max_pe: parseFloat(document.getElementById('slider-max-pe').value),
            max_de: parseFloat(document.getElementById('slider-max-de').value),
            conjunctive: document.getElementById('chk-conjunctive').checked,
            value_trap_filter: document.getElementById('chk-value-trap').checked,
            winsorize: document.getElementById('chk-value-trap').checked,
            enforce_full_cycle: document.getElementById('chk-value-trap').checked,
            use_cyclically_adjusted_ey: document.getElementById('chk-value-trap').checked,
            gating_200w: document.getElementById('chk-gating200w').checked,
            cluster_boost: document.getElementById('chk-cluster-boost').checked,
            csuite_haircut: document.getElementById('chk-csuite-haircut').checked,
            min_insider_buys: parseFloat(document.getElementById('slider-min-insider').value),
            include_insider: true
        };
    }

    async function triggerScan() {
        if (state.isScanning) return;
        state.isScanning = true;
        const scanBtn = document.getElementById('btn-rescan-now');
        if (scanBtn) scanBtn.textContent = '⏳ Scanning...';

        const params = gatherScanParameters();

        try {
            const resp = await fetch('/api/munger/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(params)
            });

            if (resp.ok) {
                const data = await resp.json();
                state.candidates = data.candidates || [];
                updateKPICards(data);
                renderScreenerTable();
                showToast(`Screened ${data.total_screened} stocks! Found ${data.strike_zone_count} in Strike Zone.`, "🎯");
            } else {
                throw new Error("Scan request failed");
            }
        } catch (e) {
            console.warn("Scan API offline, loading embedded institutional seed data...", e);
            loadSeedCandidates();
        } finally {
            state.isScanning = false;
            if (scanBtn) scanBtn.textContent = '🔄 Re-Scan';
        }
    }

    const btnScanTrigger = document.getElementById('btn-trigger-scan');
    if (btnScanTrigger) btnScanTrigger.addEventListener('click', triggerScan);
    const btnRescan = document.getElementById('btn-rescan-now');
    if (btnRescan) btnRescan.addEventListener('click', triggerScan);

    function updateKPICards(data) {
        const cands = state.candidates || [];
        const total = data.total_screened || cands.length || 176;
        const strike = cands.filter(c => c.is_strike_zone).length || data.strike_zone_count || 0;
        const traps = cands.filter(c => c.is_trap).length || data.cyclical_traps_count || 0;
        const clusters = cands.filter(c => c.insider_mult > 1.0).length || data.cluster_buys_count || 0;
        const insiderConviction = cands.filter(c => c.insider_mult > 1.0 || (c.insider_buys_usd > 0 && c.insider_mult >= 1.0)).length;
        const allPassed = cands.filter(c => c.all_med).length;
        const watchlist = total - strike - traps;

        document.getElementById('kpi-total-screened').textContent = total;
        document.getElementById('kpi-strike-zone').textContent = strike;
        document.getElementById('kpi-cyclical-traps').textContent = traps;
        document.getElementById('kpi-insider-flagged').textContent = insiderConviction || (data.cluster_buys_count || 0) + (data.csuite_haircuts_count || 0);

        if (document.getElementById('cnt-all')) document.getElementById('cnt-all').textContent = total;
        if (document.getElementById('cnt-strike')) document.getElementById('cnt-strike').textContent = strike;
        if (document.getElementById('cnt-all-passed')) document.getElementById('cnt-all-passed').textContent = allPassed;
        if (document.getElementById('cnt-insider')) document.getElementById('cnt-insider').textContent = insiderConviction;
        if (document.getElementById('cnt-traps')) document.getElementById('cnt-traps').textContent = traps;
        if (document.getElementById('cnt-cluster')) document.getElementById('cnt-cluster').textContent = clusters;
        if (document.getElementById('cnt-watch')) document.getElementById('cnt-watch').textContent = watchlist;
    }

    // --------------------------------------------------------------------------
    // 7. SCREENER TABLE RENDERING & 4-PILLAR COLOR CODING
    // --------------------------------------------------------------------------
    function renderScreenerTable() {
        const tbody = document.getElementById('screener-table-body');
        if (!tbody) return;

        const sMinGrowth = parseFloat(document.getElementById('slider-min-sales5y')?.value || 0);
        const sMinRoic = parseFloat(document.getElementById('slider-min-roic')?.value || 0);
        const sMaxPe = parseFloat(document.getElementById('slider-max-pe')?.value || 80);
        const sMaxDe = parseFloat(document.getElementById('slider-max-de')?.value || 500);

        let filtered = state.candidates.filter(c => {
            // Filter chips
            if (state.activeFilter === 'strike' && !c.is_strike_zone) return false;
            if (state.activeFilter === 'all-passed' && !c.all_med) return false;
            if (state.activeFilter === 'insider' && !(c.insider_mult > 1.0 || (c.insider_buys_usd > 0 && c.insider_mult >= 1.0))) return false;
            if (state.activeFilter === 'traps' && !c.is_trap) return false;
            if (state.activeFilter === 'cluster' && c.insider_mult <= 1.0) return false;
            if (state.activeFilter === 'watchlist' && (c.is_strike_zone || c.is_trap)) return false;

            // Dedicated Insider Conviction toggle button
            if (state.insiderFilterActive && !(c.insider_mult > 1.0 || (c.insider_buys_usd > 0 && c.insider_mult >= 1.0))) return false;

            // Search box
            if (state.searchQuery) {
                const q = state.searchQuery.toLowerCase();
                const mTicker = c.ticker.toLowerCase().includes(q);
                const mName = (c.name || '').toLowerCase().includes(q);
                const mSector = (c.sector || '').toLowerCase().includes(q);
                if (!mTicker && !mName && !mSector) return false;
            }

            return true;
        });

        // Sorting
        filtered.sort((a, b) => {
            let valA = a[state.sortColumn];
            let valB = b[state.sortColumn];
            if (typeof valA === 'string') {
                return state.sortAsc ? valA.localeCompare(valB) : valB.localeCompare(valA);
            }
            return state.sortAsc ? (valA - valB) : (valB - valA);
        });

        if (filtered.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="15" style="text-align: center; padding: 2.5rem; color: var(--text-muted);">
                        No candidates match the active filter or search query.
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = '';
        filtered.forEach(c => {
            const tr = document.createElement('tr');
            const isSelected = state.selectedBuys.has(c.ticker);
            if (isSelected) tr.classList.add('selected');

            // 4-Pillar Qualifications against Universe Medians & Sliders:
            // Median 1: Growth >= 12.8%
            // Median 2: ROIC >= 18.2%
            // Median 3: PE <= 27.4x
            // Median 4: DE <= 55.2%
            const passGrowth = c.pillar_pass ? c.pillar_pass.growth : (c.sales5y >= 12.8 && c.sales5y >= sMinGrowth);
            const passRoic = c.pillar_pass ? c.pillar_pass.roic : (c.roic >= 18.2 && c.roic >= sMinRoic);
            const passPe = c.pillar_pass ? c.pillar_pass.pe : (c.pe > 0 && c.pe <= 27.4 && c.pe <= sMaxPe);
            const passDe = c.pillar_pass ? c.pillar_pass.de : (c.de <= 55.2 && c.de <= sMaxDe);

            const passedCount = (passGrowth ? 1 : 0) + (passRoic ? 1 : 0) + (passPe ? 1 : 0) + (passDe ? 1 : 0);
            const passAll = Boolean(c.all_med || passedCount === 4);

            let all4Badge = '';
            if (passAll) {
                all4Badge = `<span class="badge badge-pass-all" title="Passed all 4 factor hurdles simultaneously!">✅ 4/4 PASS</span>`;
            } else if (passedCount === 3) {
                const missed = !passGrowth ? 'Growth' : (!passRoic ? 'ROIC' : (!passPe ? 'P/E' : 'D/E'));
                all4Badge = `<span class="badge badge-pass-partial" title="Passed 3 of 4 pillars (Missed: ${missed})">⚠️ 3/4 (${missed})</span>`;
            } else {
                all4Badge = `<span class="badge badge-pass-fail" title="Passed ${passedCount} of 4 factor hurdles">❌ ${passedCount}/4</span>`;
            }

            let badgeHtml = '';
            if (c.is_strike_zone) {
                if (c.insider_mult > 1.0) {
                    badgeHtml = `<span class="badge badge-strike">⭐ STRIKE ZONE (${c.insider_mult.toFixed(2)}x)</span>`;
                } else if (c.insider_mult < 1.0) {
                    badgeHtml = `<span class="badge badge-haircut">⚠️ STRIKE (HAIRCUT ${c.insider_mult.toFixed(2)}x)</span>`;
                } else {
                    badgeHtml = `<span class="badge badge-strike">⭐ STRIKE ZONE</span>`;
                }
            } else if (c.is_trap) {
                badgeHtml = `<span class="badge badge-trap">🛡️ TRAP BLOCKED</span>`;
            } else {
                badgeHtml = `<span class="badge badge-watchlist">Watchlist</span>`;
            }

            let insiderBadge = '<span style="color: var(--text-muted); font-size: 0.8rem;">1.00x</span>';
            if (c.insider_mult > 1.0) {
                insiderBadge = `<span class="badge badge-boost">+15% (${c.insider_mult.toFixed(2)}x)</span>`;
            } else if (c.insider_mult < 1.0) {
                insiderBadge = `<span class="badge badge-haircut">-30% (${c.insider_mult.toFixed(2)}x)</span>`;
            }

            tr.innerHTML = `
                <td style="text-align: center;">
                    <input type="checkbox" class="candidate-select" data-ticker="${c.ticker}" ${isSelected ? 'checked' : ''}>
                </td>
                <td><strong class="mono" style="color: #60a5fa;">${c.ticker}</strong></td>
                <td style="max-width: 160px; overflow: hidden; text-overflow: ellipsis;" title="${c.name || c.ticker}">${c.name || c.ticker}</td>
                <td style="text-align: right;" class="mono">$${c.spot.toFixed(2)}</td>

                <!-- Pillar 1: Growth -->
                <td style="text-align: right;" class="mono ${passGrowth ? 'cell-pillar-pass' : 'cell-pillar-fail'}" title="${passGrowth ? 'Pillar 1 Pass: ≥ 12.8% median' : 'Pillar 1 Fail: < 12.8% median'}">
                    ${c.sales5y.toFixed(1)}%
                </td>

                <!-- Pillar 2: ROIC -->
                <td style="text-align: right;" class="mono ${passRoic ? 'cell-pillar-pass' : 'cell-pillar-fail'}" title="${passRoic ? 'Pillar 2 Pass: ≥ 18.2% median' : 'Pillar 2 Fail: < 18.2% median'}">
                    ${c.roic.toFixed(1)}%
                </td>

                <!-- Pillar 3: P/E -->
                <td style="text-align: right;" class="mono ${passPe ? 'cell-pillar-pass' : 'cell-pillar-fail'}" title="${passPe ? 'Pillar 3 Pass: ≤ 27.4x median' : 'Pillar 3 Fail: > 27.4x median'}">
                    ${c.pe.toFixed(1)}x
                </td>

                <!-- Pillar 4: Net Debt / EBITDA -->
                <td style="text-align: right;" class="mono ${passDe ? 'cell-pillar-pass' : 'cell-pillar-fail'}" title="${passDe ? 'Pillar 4 Pass: Net Debt/EBITDA ≤ 2.5x (Median: 1.06x)' : 'Pillar 4 Fail: Net Debt/EBITDA > 2.5x'}">
                    ${c.nd_ebitda !== undefined ? (c.nd_ebitda <= 0 ? '<span style=\"color:#34d399;\">NET CASH</span>' : `${c.nd_ebitda.toFixed(1)}x`) : `${c.de.toFixed(1)}%`}
                </td>

                <!-- All 4 Pillars Gate -->
                <td style="text-align: center;">${all4Badge}</td>

                <td style="text-align: right; color: var(--text-muted);" class="mono">${c.raw_m.toFixed(2)}</td>
                <td style="text-align: right;" class="mono" style="font-weight: 600; color: ${c.clean_m >= 2.0 ? '#38bdf8' : 'inherit'}">${c.clean_m.toFixed(2)}</td>
                <td style="text-align: center;">${insiderBadge}</td>
                <td style="text-align: right;" class="mono" style="font-weight: 700; color: ${c.conviction_m >= 2.0 ? '#34d399' : 'inherit'}; font-size: 0.95rem;">${c.conviction_m.toFixed(2)}</td>
                <td style="text-align: center;">${badgeHtml}</td>
                <td style="text-align: center;">
                    <button class="btn-action btn-secondary btn-inspect" data-ticker="${c.ticker}" style="padding: 4px 8px; font-size: 0.75rem;">
                        Inspect
                    </button>
                </td>
            `;

            tbody.appendChild(tr);
        });

        // Attach selection listeners
        tbody.querySelectorAll('.candidate-select').forEach(box => {
            box.addEventListener('change', (e) => {
                const sym = e.target.getAttribute('data-ticker');
                if (e.target.checked) {
                    state.selectedBuys.add(sym);
                } else {
                    state.selectedBuys.delete(sym);
                }
                renderScreenerTable();
            });
        });

        // Attach Inspect listeners
        tbody.querySelectorAll('.btn-inspect').forEach(btn => {
            btn.addEventListener('click', () => {
                const sym = btn.getAttribute('data-ticker');
                const candidate = state.candidates.find(c => c.ticker === sym);
                if (candidate) openStockModal(candidate);
            });
        });
    }

    // Dedicated Insider Conviction Toggle Button
    const btnToggleInsider = document.getElementById('btn-toggle-insider-filter');
    if (btnToggleInsider) {
        btnToggleInsider.addEventListener('click', () => {
            state.insiderFilterActive = !state.insiderFilterActive;
            if (state.insiderFilterActive) {
                btnToggleInsider.classList.remove('btn-secondary');
                btnToggleInsider.classList.add('btn-emerald');
                btnToggleInsider.textContent = '🕵️ Insider Filter: ON';
                showToast("Filtered matrix to high insider conviction candidates!", "🕵️");
            } else {
                btnToggleInsider.classList.remove('btn-emerald');
                btnToggleInsider.classList.add('btn-secondary');
                btnToggleInsider.textContent = '🕵️ Insider Filter: OFF';
                showToast("Showing all candidates across insider profiles.", "ℹ️");
            }
            renderScreenerTable();
        });
    }

    // Filter chip clicks
    document.querySelectorAll('.filter-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            document.querySelectorAll('.filter-chip').forEach(c => c.classList.remove('active'));
            chip.classList.add('active');
            state.activeFilter = chip.getAttribute('data-filter');
            renderScreenerTable();
        });
    });

    // Search input
    const searchInput = document.getElementById('screener-search');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            state.searchQuery = e.target.value;
            renderScreenerTable();
        });
    }

    // Table header sort
    document.querySelectorAll('#screener-table th[data-sort]').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.getAttribute('data-sort');
            if (state.sortColumn === col) {
                state.sortAsc = !state.sortAsc;
            } else {
                state.sortColumn = col;
                state.sortAsc = false;
            }
            renderScreenerTable();
        });
    });

    // Select all strike zone
    const btnSelectAll = document.getElementById('btn-select-all-strike');
    if (btnSelectAll) {
        btnSelectAll.addEventListener('click', () => {
            state.candidates.forEach(c => {
                if (c.is_strike_zone) state.selectedBuys.add(c.ticker);
            });
            renderScreenerTable();
            showToast(`Selected all ${state.selectedBuys.size} Strike Zone stocks for target orders!`, "☑️");
        });
    }

    // --------------------------------------------------------------------------
    // 8. STOCK MOAT DEEP DIVE MODAL & RADAR CHART
    // --------------------------------------------------------------------------
    function openStockModal(c) {
        const modal = document.getElementById('stock-modal');
        if (!modal) return;

        document.getElementById('modal-stock-title').textContent = `${c.name || c.ticker} (${c.ticker})`;
        document.getElementById('modal-stock-subtitle').textContent = `${c.sector || 'Sector'} • ${c.theme || 'Quality'} • Spot: $${c.spot.toFixed(2)}`;

        document.getElementById('modal-sales5y').textContent = `${c.sales5y.toFixed(1)}%`;
        document.getElementById('modal-roic').textContent = `${c.roic.toFixed(1)}%`;
        document.getElementById('modal-valuation').textContent = `${c.pe.toFixed(1)}x / ${c.pb.toFixed(2)}x`;
        if (c.nd_ebitda !== undefined) {
            const ndStr = c.nd_ebitda <= 0 ? "Net Cash (Fortress)" : `${c.nd_ebitda.toFixed(2)}x`;
            document.getElementById('modal-solvency').textContent = `${ndStr} (D/E: ${c.de.toFixed(1)}%)`;
        } else {
            document.getElementById('modal-solvency').textContent = `${c.de.toFixed(1)}%`;
        }

        const badge = document.getElementById('modal-insider-badge');
        if (c.insider_mult > 1.0) {
            badge.className = "badge badge-boost";
            badge.textContent = `CLUSTER BUY (+15% / ${c.insider_mult.toFixed(2)}x)`;
        } else if (c.insider_mult < 1.0) {
            badge.className = "badge badge-haircut";
            badge.textContent = `C-SUITE HAIRCUT (-30% / ${c.insider_mult.toFixed(2)}x)`;
        } else {
            badge.className = "badge badge-watchlist";
            badge.textContent = `NEUTRAL (${c.insider_mult.toFixed(2)}x)`;
        }

        document.getElementById('modal-insider-notes').textContent = c.insider_notes || "Balanced open-market insider activity.";
        document.getElementById('modal-insider-buys').textContent = `$${Math.round(c.insider_buys_usd || 0).toLocaleString()}`;
        document.getElementById('modal-insider-sales').textContent = `$${Math.round(c.insider_sales_usd || 0).toLocaleString()}`;

        document.getElementById('modal-link-edgar').href = `https://www.sec.gov/edgar/searchedgar/companysearch?companyName=${c.ticker}`;
        document.getElementById('modal-link-research').href = c.fidelity_research || `https://digital.fidelity.com/prgw/digital/research/quote/dashboard/summary?symbol=${c.ticker}`;
        document.getElementById('modal-link-trade').href = c.fidelity_deeplink || `https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=${c.ticker}`;

        // Render Radar Chart
        renderRadarChart(c);

        modal.classList.add('active');
    }

    function renderRadarChart(c) {
        const canvas = document.getElementById('stock-radar-chart');
        if (!canvas) return;

        if (state.radarChart) {
            state.radarChart.destroy();
        }

        const radarData = c.radar || { growth: 75, roic: 85, valuation: 45, solvency: 70 };

        state.radarChart = new Chart(canvas, {
            type: 'radar',
            data: {
                labels: ['5Y Growth (g)', 'ROIC (r)', 'Earnings Yield (y)', 'Solvency (s)'],
                datasets: [
                    {
                        label: `${c.ticker} Percentile`,
                        data: [radarData.growth, radarData.roic, radarData.valuation, radarData.solvency],
                        backgroundColor: 'rgba(59, 130, 246, 0.25)',
                        borderColor: '#3b82f6',
                        borderWidth: 2,
                        pointBackgroundColor: '#60a5fa',
                    },
                    {
                        label: 'S&P 100 Median Benchmark (50th %ile)',
                        data: [50, 50, 50, 50],
                        borderColor: 'rgba(255, 255, 255, 0.2)',
                        borderWidth: 1,
                        borderDash: [4, 4],
                        pointRadius: 0,
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    r: {
                        angleLines: { color: 'rgba(255, 255, 255, 0.08)' },
                        grid: { color: 'rgba(255, 255, 255, 0.08)' },
                        pointLabels: { color: '#94a3b8', font: { size: 10, family: 'Inter' } },
                        ticks: { display: false, min: 0, max: 100 }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    }
                }
            }
        });
    }

    const btnCloseModal = document.getElementById('btn-close-modal');
    if (btnCloseModal) {
        btnCloseModal.addEventListener('click', () => {
            document.getElementById('stock-modal').classList.remove('active');
        });
    }

    // --------------------------------------------------------------------------
    // 9. FIDELITY RECONCILIATION API & TAX DEFERRAL
    // --------------------------------------------------------------------------
    async function loadFidelityReconciliation(csvText = null) {
        try {
            const opts = csvText ? {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ csv_text: csvText })
            } : { method: 'POST' };

            const resp = await fetch('/api/munger/reconcile-fidelity', opts);
            if (resp.ok) {
                const data = await resp.json();
                state.lots = data.lots || [];
                renderFidelityLots(data);
                showToast(`Reconciled ${state.lots.length} Fidelity tax lots with live prices!`, "🏦");
            }
        } catch (e) {
            console.warn("Fidelity API offline, using seed portfolio lots...", e);
            loadSeedFidelityLots();
        }
    }

    function renderFidelityLots(data) {
        const sum = data.summary || {};
        const totalVal = sum.total_value || 222482.63;
        const totalBasis = sum.total_basis || 54500.0;
        const totalGain = sum.total_gain || 167982.63;
        const gainPct = sum.total_gain_pct || 308.22;
        const taxSavings = sum.potential_tax_savings || 34500.0;

        document.getElementById('header-net-worth').textContent = `$${Math.round(totalVal).toLocaleString()}`;
        document.getElementById('port-total-val').textContent = `$${totalVal.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        document.getElementById('port-total-basis').textContent = `$${totalBasis.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
        document.getElementById('port-total-gain').textContent = `+$${totalGain.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} (+${gainPct.toFixed(1)}%)`;
        document.getElementById('port-tax-savings').textContent = `$${taxSavings.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

        const tbody = document.getElementById('fidelity-lots-body');
        if (!tbody) return;

        if (state.lots.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="11" style="text-align: center; padding: 2rem; color: var(--text-muted);">
                        No lots loaded.
                    </td>
                </tr>
            `;
            return;
        }

        tbody.innerHTML = '';
        state.lots.forEach(lot => {
            const tr = document.createElement('tr');

            let actionBadge = '';
            if (lot.action.includes('STRONG HOLD') || lot.action.includes('COMPOUND')) {
                actionBadge = `<span class="badge badge-strike">⭐ ${lot.action}</span>`;
            } else if (lot.action.includes('HOLD FOR TAX DEFERRAL')) {
                actionBadge = `<span class="badge badge-haircut">⏳ ${lot.action}</span>`;
            } else if (lot.action.includes('DYNAMIC MOAT EXIT') || lot.action.includes('SELL')) {
                actionBadge = `<span class="badge badge-trap">🚨 ${lot.action}</span>`;
            } else {
                actionBadge = `<span class="badge badge-watchlist">${lot.action}</span>`;
            }

            const daysLeft = lot.days_to_ltcg !== undefined ? lot.days_to_ltcg : Math.max(0, 365 - lot.days_held);
            const taxBadge = lot.ltcg_status === 'LTCG' 
                ? `<span class="badge badge-ltcg" title="LTCG Qualified: Max federal tax rate 23.8%">💎 LTCG (≥365d)</span>` 
                : `<span class="badge badge-stcg" title="STCG: Ordinary income tax rate up to 40.8%. Hold to day 366.">⏳ STCG (${daysLeft}d left)</span>`;

            tr.innerHTML = `
                <td><strong class="mono" style="color: #60a5fa;">${lot.symbol}</strong></td>
                <td style="text-align: right;" class="mono">${lot.shares.toFixed(1)}</td>
                <td style="text-align: right;" class="mono">$${lot.cost_basis.toFixed(2)}</td>
                <td style="text-align: right;" class="mono">$${lot.live_price.toFixed(2)}</td>
                <td style="text-align: right;" class="mono" style="font-weight: 600;">$${lot.live_value.toFixed(2)}</td>
                <td style="text-align: right;" class="mono" style="color: ${lot.unrealized_gain >= 0 ? '#34d399' : '#f87171'}">
                    ${lot.unrealized_gain >= 0 ? '+' : ''}$${lot.unrealized_gain.toFixed(2)} (${lot.unrealized_gain_pct.toFixed(1)}%)
                </td>
                <td style="text-align: center;" class="mono">${lot.days_held}d</td>
                <td style="text-align: center;">${taxBadge}</td>
                <td style="text-align: center;">${actionBadge}</td>
                <td style="font-size: 0.76rem; color: var(--text-secondary); max-width: 250px;">${lot.rationale}</td>
                <td style="text-align: center;">
                    <a href="${lot.fidelity_deeplink}" target="_blank" class="btn-action btn-secondary" style="padding: 4px 8px; font-size: 0.74rem;">
                        Trade ↗
                    </a>
                </td>
            `;

            tbody.appendChild(tr);
        });
    }

    const btnLoadSample = document.getElementById('btn-load-sample-csv');
    if (btnLoadSample) {
        btnLoadSample.addEventListener('click', () => {
            loadFidelityReconciliation();
        });
    }

    // File Drop Zone
    const dropZone = document.getElementById('csv-drop-zone');
    const fileInput = document.getElementById('file-csv-input');

    if (dropZone && fileInput) {
        dropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        });

        dropZone.addEventListener('dragleave', () => {
            dropZone.classList.remove('dragover');
        });

        dropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length > 0) {
                handleUploadedCSV(e.dataTransfer.files[0]);
            }
        });

        fileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleUploadedCSV(e.target.files[0]);
            }
        });
    }

    function handleUploadedCSV(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            const content = e.target.result;
            loadFidelityReconciliation(content);
        };
        reader.readAsText(file);
    }

    // Modal Import
    const btnOpenFid = document.getElementById('btn-open-fidelity-modal');
    const modalFid = document.getElementById('fidelity-modal');
    const btnCloseFid = document.getElementById('btn-close-fidelity-modal');
    const btnCancelFid = document.getElementById('btn-cancel-fidelity-modal');
    const btnSubmitFid = document.getElementById('btn-submit-fidelity-csv');

    if (btnOpenFid && modalFid) {
        btnOpenFid.addEventListener('click', () => modalFid.classList.add('active'));
    }
    if (btnCloseFid && modalFid) {
        btnCloseFid.addEventListener('click', () => modalFid.classList.remove('active'));
    }
    if (btnCancelFid && modalFid) {
        btnCancelFid.addEventListener('click', () => modalFid.classList.remove('active'));
    }
    const btnModalSample = document.getElementById('btn-modal-load-sample');
    if (btnModalSample && modalFid) {
        btnModalSample.addEventListener('click', () => {
            loadFidelityReconciliation();
            modalFid.classList.remove('active');
        });
    }
    if (btnSubmitFid && modalFid) {
        btnSubmitFid.addEventListener('click', () => {
            const txt = document.getElementById('fidelity-csv-textarea').value;
            if (txt.trim()) {
                loadFidelityReconciliation(txt);
                modalFid.classList.remove('active');
            } else {
                showToast("Please paste CSV text or browse a file.", "⚠️");
            }
        });
    }

    // --------------------------------------------------------------------------
    // 9B. AUTOMATIC LOCAL FILE-WATCHER DAEMON POLLER & TRIGGER
    // --------------------------------------------------------------------------
    let lastWatcherFileTime = null;

    async function pollWatcherStatus(manualTrigger = false) {
        try {
            const url = manualTrigger ? '/api/munger/watcher-trigger' : '/api/munger/watcher-status';
            const opts = manualTrigger ? { method: 'POST' } : { method: 'GET' };
            const resp = await fetch(url, opts);
            if (!resp.ok) return;

            const data = await resp.json();
            const watcherState = manualTrigger ? (data.watcher_state || {}) : data;

            // Update UI banner elements
            const watchDirsEl = document.getElementById('watcher-watch-dirs');
            const lastFileEl = document.getElementById('watcher-last-file');
            const syncStatusEl = document.getElementById('watcher-sync-status');

            if (watchDirsEl && watcherState.watch_dirs) {
                const shortDirs = watcherState.watch_dirs.map(d => d.replace(/.*Users\\[^\\]+/, '~')).join(', ');
                watchDirsEl.textContent = `📁 Watching: ${shortDirs}`;
            }

            if (lastFileEl) {
                if (watcherState.last_detected_file) {
                    lastFileEl.textContent = `📄 Ingested: ${watcherState.last_detected_file} (${watcherState.last_detected_time || 'Just now'})`;
                    lastFileEl.style.color = '#34d399';
                } else {
                    lastFileEl.textContent = `📄 Last Ingested: None yet`;
                }
            }

            if (syncStatusEl) {
                syncStatusEl.textContent = watcherState.active 
                    ? `⏱️ Polling: Active (${watcherState.status || 'OK'})`
                    : `⏱️ Polling: Background Daemon`;
            }

            // If a newly detected file has arrived with reconciliation results
            if (watcherState.last_detected_time && watcherState.last_detected_time !== lastWatcherFileTime) {
                lastWatcherFileTime = watcherState.last_detected_time;
                if (watcherState.recon_result && watcherState.recon_result.lots) {
                    state.lots = watcherState.recon_result.lots;
                    renderFidelityLots(watcherState.recon_result);
                    showToast(`⚡ Daemon ingested ${watcherState.last_detected_file}! (${state.lots.length} lots reconciled)`, "⚡", 4000);
                } else {
                    loadFidelityReconciliation();
                }
            } else if (manualTrigger) {
                if (data.reconciled) {
                    showToast(`⚡ Ingested new CSV from Downloads!`, "⚡", 3500);
                } else {
                    showToast(`Downloads scanned: no new un-ingested Fidelity CSV found.`, "ℹ️", 3000);
                }
            }
        } catch (e) {
            console.debug("Watcher poll status error (offline mode):", e);
        }
    }

    const btnTriggerWatcher = document.getElementById('btn-trigger-watcher-check');
    if (btnTriggerWatcher) {
        btnTriggerWatcher.addEventListener('click', () => {
            btnTriggerWatcher.disabled = true;
            btnTriggerWatcher.textContent = "⏳ Scanning...";
            pollWatcherStatus(true).finally(() => {
                btnTriggerWatcher.disabled = false;
                btnTriggerWatcher.textContent = "📂 Check Downloads Now";
            });
        });
    }

    // --------------------------------------------------------------------------
    // 10. ORDER GENERATION API & COPYABLE TICKETS
    // --------------------------------------------------------------------------
    async function generateOrders() {
        const container = document.getElementById('generated-orders-container');
        if (!container) return;

        // Collect buy candidates
        let buys = Array.from(state.selectedBuys);
        if (buys.length === 0) {
            // Default to top 2 Strike-Zone compounders if none explicitly selected
            state.candidates.forEach(c => {
                if (c.is_strike_zone) buys.push(c.ticker);
            });
        }
        if (buys.length === 0) {
            buys = ['LLY', 'FTNT'];
        }

        // Collect lots to sell (lots with Action == SELL (MOC))
        const sells = state.lots.filter(l => l.action.includes('SELL'));

        const payload = {
            selected_buys: buys,
            lots_to_sell: sells,
            available_cash: parseFloat(document.getElementById('order-avail-cash').value) || 100000,
            total_portfolio_value: 222482.63,
            max_alloc_pct: parseFloat(document.getElementById('slider-max-alloc').value) || 15.0,
            cash_buffer_pct: parseFloat(document.getElementById('slider-cash-buffer').value) || 5.0,
            overlay_8020: state.overlay8020Active,
        };

        try {
            const resp = await fetch('/api/munger/generate-orders', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            if (resp.ok) {
                const data = await resp.json();
                renderGeneratedOrders(data.orders || [], data.orders_csv || "");
                return;
            }
        } catch (e) {
            console.warn("Order generator API offline, generating local client tickets...", e);
        }

        // Local fallback ticket generation
        generateLocalOrderTickets(buys, sells, payload);
    }

    function renderGeneratedOrders(orders, ordersCsv) {
        const container = document.getElementById('generated-orders-container');
        if (!container) return;

        if (orders.length === 0) {
            container.innerHTML = `
                <div style="text-align: center; padding: 2rem; color: var(--text-muted);">
                    No orders needed. All holdings are within target allocation boundaries.
                </div>
            `;
            return;
        }

        container.innerHTML = '';
        orders.forEach(o => {
            const card = document.createElement('div');
            card.className = 'ticket-card';
            const isBuy = o.action === 'BUY';

            card.innerHTML = `
                <div class="ticket-header">
                    <div>
                        <span class="badge ${isBuy ? 'badge-strike' : 'badge-trap'}">${o.action}</span>
                        <strong class="mono" style="font-size: 1.05rem; margin-left: 8px; color: #f8fafc;">${o.symbol}</strong>
                        <span style="font-size: 0.8rem; color: var(--text-secondary); margin-left: 6px;">(${o.shares} Shares @ ~\$${o.limit_price.toFixed(2)})</span>
                    </div>
                    <span class="mono" style="font-size: 0.95rem; font-weight: 600; color: ${isBuy ? '#38bdf8' : '#f87171'};">
                        ~\$${o.est_value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                </div>

                <div class="ticket-string">
                    <span>${o.fidelity_order_string}</span>
                    <button class="btn-action btn-secondary btn-copy-single" data-ticket="${o.fidelity_order_string}" style="padding: 3px 8px; font-size: 0.74rem;">
                        📋 Copy
                    </button>
                </div>

                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 6px;">
                    <span style="font-size: 0.76rem; color: var(--text-muted);">${o.rationale}</span>
                    <a href="${o.fidelity_deeplink}" target="_blank" class="btn-action btn-emerald" style="padding: 4px 10px; font-size: 0.78rem;">
                        Execute on Fidelity ↗
                    </a>
                </div>
            `;
            container.appendChild(card);
        });

        // Copy single ticket listeners
        container.querySelectorAll('.btn-copy-single').forEach(b => {
            b.addEventListener('click', () => {
                const str = b.getAttribute('data-ticket');
                navigator.clipboard.writeText(str);
                showToast(`Copied: ${str}`, "📋");
            });
        });

        // Store CSV for download
        window._currentOrdersCsv = ordersCsv;
    }

    function generateLocalOrderTickets(buys, sells, payload) {
        const orders = [];
        sells.forEach(s => {
            const sym = s.symbol;
            const shares = s.shares;
            const spot = s.live_price || 345.85;
            orders.push({
                symbol: sym,
                action: "SELL",
                order_type: "MOC",
                shares: shares,
                limit_price: spot,
                est_value: shares * spot,
                rationale: s.rationale || "Munger criteria decay / tax-loss harvest.",
                fidelity_order_string: `SELL ${sym} ${shares} SHARES MOC DAY`,
                fidelity_deeplink: `https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=${sym}`
            });
        });

        buys.forEach(b => {
            const sym = b;
            const spot = sym === 'LLY' ? 1208.25 : (sym === 'FTNT' ? 186.46 : (sym === 'NVDA' ? 214.13 : 140.0));
            const baseShares = sym === 'LLY' ? 25 : (sym === 'FTNT' ? 120 : 100);

            if (state.overlay8020Active) {
                // 80% Core Stock
                const eqShares = Math.max(1, Math.floor(baseShares * 0.80));
                const eqVal = eqShares * spot;
                orders.push({
                    symbol: sym,
                    action: "BUY",
                    order_type: "MOC",
                    shares: eqShares,
                    limit_price: spot,
                    est_value: eqVal,
                    rationale: "80% Core Physical Stock (Tax-deferred compounder).",
                    fidelity_order_string: `BUY ${sym} ${eqShares} SHARES MOC DAY`,
                    fidelity_deeplink: `https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=${sym}`
                });

                // 20% Call Spread (95C / 110C)
                const k95 = (spot * 0.95).toFixed(1);
                const k110 = (spot * 1.10).toFixed(1);
                const debit = (spot * 0.065).toFixed(2);
                const contracts = Math.max(1, Math.floor((baseShares * 0.20 * spot) / (parseFloat(debit) * 100)));
                const spreadVal = contracts * parseFloat(debit) * 100;
                const spreadTicket = `BUY VERTICAL CALL SPREAD ${sym} x${contracts}: BUY ${k95} CALL / SELL ${k110} CALL EXP 1Y (NET DEBIT $${debit})`;

                orders.push({
                    symbol: sym,
                    action: "BUY (CALL SPREAD)",
                    order_type: "NET DEBIT",
                    shares: contracts,
                    limit_price: parseFloat(debit),
                    est_value: spreadVal,
                    rationale: `20% Asymmetric Options Overlay (95C/110C Bull Spread: Max ROC +133.6%).`,
                    fidelity_order_string: spreadTicket,
                    fidelity_deeplink: `https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=${sym}`,
                    is_options_spread: true
                });
            } else {
                orders.push({
                    symbol: sym,
                    action: "BUY",
                    order_type: "MOC",
                    shares: baseShares,
                    limit_price: spot,
                    est_value: baseShares * spot,
                    rationale: "Munger Strike Zone compounder (Target 15% cap).",
                    fidelity_order_string: `BUY ${sym} ${baseShares} SHARES MOC DAY`,
                    fidelity_deeplink: `https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=${sym}`
                });
            }
        });

        let csv = "Action,Symbol,Quantity,Order_Type,Time_In_Force,Limit_Price,Estimated_Value,Rationale\n";
        orders.forEach(o => {
            csv += `${o.action},${o.symbol},${o.shares},${o.order_type},DAY,${o.limit_price},${o.est_value},"${o.rationale}"\n`;
        });

        renderGeneratedOrders(orders, csv);
    }

    // Copy All Orders Button
    const btnCopyAll = document.getElementById('btn-copy-all-orders');
    if (btnCopyAll) {
        btnCopyAll.addEventListener('click', () => {
            const cards = document.querySelectorAll('.ticket-string span');
            if (cards.length === 0) return;
            const allText = Array.from(cards).map(c => c.textContent).join('\n');
            navigator.clipboard.writeText(allText);
            showToast(`Copied ${cards.length} Fidelity order strings to clipboard!`, "📋");
        });
    }

    // Export CSV Button
    const btnExportCsv = document.getElementById('btn-export-orders-csv');
    if (btnExportCsv) {
        btnExportCsv.addEventListener('click', () => {
            const csv = window._currentOrdersCsv;
            if (!csv) {
                showToast("No orders available to export.", "⚠️");
                return;
            }
            const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `fidelity_munger_orders_${new Date().toISOString().slice(0,10)}.csv`;
            a.click();
            URL.revokeObjectURL(url);
            showToast("Downloaded Fidelity Orders CSV!", "📥");
        });
    }

    // 80/20 Overlay Checkbox Listener in Stage 6
    const chkOverlay8020 = document.getElementById('chk-overlay-8020');
    const cardOverlay8020 = document.getElementById('card-overlay-8020');
    if (chkOverlay8020) {
        chkOverlay8020.addEventListener('change', () => {
            state.overlay8020Active = chkOverlay8020.checked;
            if (cardOverlay8020) {
                if (chkOverlay8020.checked) {
                    cardOverlay8020.classList.add('active');
                } else {
                    cardOverlay8020.classList.remove('active');
                }
            }
            showToast(`80/20 Options Call Overlay: ${state.overlay8020Active ? 'ENABLED' : 'DISABLED'}`, state.overlay8020Active ? '🚀' : 'ℹ️');
            generateOrders();
        });
    }

    // --------------------------------------------------------------------------
    // 11A. OPTIONS 80/20 BULL CALL SPREADS API (95C / 110C)
    // --------------------------------------------------------------------------
    async function loadOptionsSpreads() {
        try {
            const resp = await fetch('/api/munger/options-spreads');
            if (resp.ok) {
                const data = await resp.json();
                state.spreads = data.spreads || [];
                renderSpreadsTable(state.spreads);
                return;
            }
        } catch (e) {
            console.warn("Options spreads API offline, rendering local seed spreads...", e);
        }

        renderSpreadsTable([
            {
                symbol: "NVDA", spot: 214.13, dte: 365, k95: 203.4, k110: 235.5,
                net_debit: 14.14, max_loss: 1414.0, max_profit: 1796.0, max_roc_pct: 127.0,
                breakeven: 217.54, breakeven_pct: "+1.6%",
                ticket: "BUY VERTICAL CALL SPREAD NVDA x1: BUY 203.4C / SELL 235.5C EXP 1Y (NET DEBIT $14.14)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=NVDA"
            },
            {
                symbol: "AAPL", spot: 345.85, dte: 365, k95: 328.6, k110: 380.4,
                net_debit: 22.20, max_loss: 2220.0, max_profit: 2960.0, max_roc_pct: 133.3,
                breakeven: 350.80, breakeven_pct: "+1.4%",
                ticket: "BUY VERTICAL CALL SPREAD AAPL x1: BUY 328.6C / SELL 380.4C EXP 1Y (NET DEBIT $22.20)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=AAPL"
            },
            {
                symbol: "MSFT", spot: 539.11, dte: 365, k95: 512.2, k110: 593.0,
                net_debit: 34.60, max_loss: 3460.0, max_profit: 4620.0, max_roc_pct: 133.5,
                breakeven: 546.80, breakeven_pct: "+1.4%",
                ticket: "BUY VERTICAL CALL SPREAD MSFT x1: BUY 512.2C / SELL 593.0C EXP 1Y (NET DEBIT $34.60)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=MSFT"
            },
            {
                symbol: "LLY", spot: 1208.25, dte: 365, k95: 1147.8, k110: 1329.1,
                net_debit: 78.50, max_loss: 7850.0, max_profit: 10280.0, max_roc_pct: 131.0,
                breakeven: 1226.30, breakeven_pct: "+1.5%",
                ticket: "BUY VERTICAL CALL SPREAD LLY x1: BUY 1147.8C / SELL 1329.1C EXP 1Y (NET DEBIT $78.50)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=LLY"
            }
        ]);
    }

    function renderSpreadsTable(spreads) {
        const tbody = document.getElementById('spreads-table-body');
        if (!tbody) return;

        tbody.innerHTML = '';
        spreads.forEach(s => {
            const tr = document.createElement('tr');
            tr.innerHTML = `
                <td><strong class="mono" style="color: #60a5fa;">${s.symbol}</strong></td>
                <td style="text-align: right;" class="mono">$${s.spot.toFixed(2)}</td>
                <td style="text-align: center;" class="mono">${s.dte}d</td>
                <td style="text-align: right;" class="mono">$${s.k95.toFixed(1)}</td>
                <td style="text-align: right;" class="mono">$${s.k110.toFixed(1)}</td>
                <td style="text-align: right;" class="mono" style="color: #38bdf8; font-weight: 600;">$${s.net_debit.toFixed(2)}</td>
                <td style="text-align: right;" class="mono" style="color: #f87171;">$${s.max_loss.toLocaleString()}</td>
                <td style="text-align: right;" class="mono" style="color: #34d399;">+$${s.max_profit.toLocaleString()}</td>
                <td style="text-align: center;">
                    <span class="badge badge-strike" style="font-weight: 700;">+${s.max_roc_pct}%</span>
                </td>
                <td style="text-align: center;" class="mono" style="color: #fbbf24;">${s.breakeven_pct}</td>
                <td style="font-family: var(--font-mono); font-size: 0.74rem; color: #38bdf8;">
                    ${s.ticket}
                </td>
                <td style="text-align: center;">
                    <a href="${s.fidelity_deeplink}" target="_blank" class="btn-action btn-secondary" style="padding: 3px 8px; font-size: 0.74rem;">
                        Trade ↗
                    </a>
                </td>
            `;
            tbody.appendChild(tr);
        });
    }

    // Stage 7 Sub-tab switching
    const btnTabSpreads = document.getElementById('btn-tab-call-spreads');
    const btnTabCollars = document.getElementById('btn-tab-collars');
    const panelSpreads = document.getElementById('panel-options-spreads');
    const panelCollars = document.getElementById('panel-options-collars');

    if (btnTabSpreads && btnTabCollars && panelSpreads && panelCollars) {
        btnTabSpreads.addEventListener('click', () => {
            state.activeOptionsTab = 'spreads';
            btnTabSpreads.classList.remove('btn-secondary');
            btnTabSpreads.classList.add('btn-primary');
            btnTabCollars.classList.remove('btn-primary');
            btnTabCollars.classList.add('btn-secondary');
            panelSpreads.style.display = 'block';
            panelCollars.style.display = 'none';
            loadOptionsSpreads();
        });

        btnTabCollars.addEventListener('click', () => {
            state.activeOptionsTab = 'collars';
            btnTabCollars.classList.remove('btn-secondary');
            btnTabCollars.classList.add('btn-primary');
            btnTabSpreads.classList.remove('btn-primary');
            btnTabSpreads.classList.add('btn-secondary');
            panelCollars.style.display = 'block';
            panelSpreads.style.display = 'none';
            loadOptionsCollars();
        });
    }

    // Buffett Showdown & Audit Modal Listeners
    const btnOpenBuffett = document.getElementById('btn-open-buffett-modal');
    const modalBuffett = document.getElementById('buffett-showdown-modal');
    const btnCloseBuffett = document.getElementById('btn-close-buffett-modal');
    const btnModalCloseBuffett = document.getElementById('btn-modal-close-buffett');

    if (btnOpenBuffett && modalBuffett) {
        btnOpenBuffett.addEventListener('click', () => {
            modalBuffett.classList.add('active');
        });
    }
    if (btnCloseBuffett && modalBuffett) {
        btnCloseBuffett.addEventListener('click', () => {
            modalBuffett.classList.remove('active');
        });
    }
    if (btnModalCloseBuffett && modalBuffett) {
        btnModalCloseBuffett.addEventListener('click', () => {
            modalBuffett.classList.remove('active');
        });
    }

    // --------------------------------------------------------------------------
    // 11B. OPTIONS 80/20 COLLARS API
    // --------------------------------------------------------------------------
    async function loadOptionsCollars() {
        try {
            const resp = await fetch('/api/munger/options-collars');
            if (resp.ok) {
                const data = await resp.json();
                state.collars = data.collars || [];
                renderCollarsTable(state.collars);
                return;
            }
        } catch (e) {
            console.warn("Options collars API offline, rendering local seed collars...", e);
        }

        renderCollarsTable([
            {
                symbol: "NVDA", spot: 214.13, contracts: 1, shares_protected: 100, dte: 270,
                put_strike: 192.5, put_price: 12.80, call_strike: 246.0, call_price: 13.90,
                net_type: "CREDIT", net_premium_per_share: 1.10, downside_floor_pct: "-10.1%", upside_cap_pct: "+14.9%",
                collar_ticket: "COLLAR NVDA x1: BUY 192.5 PUT / SELL 246.0 CALL EXP ~9M (CREDIT $1.10/sh)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=NVDA"
            },
            {
                symbol: "AAPL", spot: 345.85, contracts: 2, shares_protected: 200, dte: 270,
                put_strike: 311.0, put_price: 15.20, call_strike: 398.0, call_price: 16.50,
                net_type: "CREDIT", net_premium_per_share: 1.30, downside_floor_pct: "-10.0%", upside_cap_pct: "+15.0%",
                collar_ticket: "COLLAR AAPL x2: BUY 311.0 PUT / SELL 398.0 CALL EXP ~9M (CREDIT $1.30/sh)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=AAPL"
            },
            {
                symbol: "MSFT", spot: 539.11, contracts: 1, shares_protected: 100, dte: 270,
                put_strike: 485.0, put_price: 24.10, call_strike: 620.0, call_price: 23.80,
                net_type: "DEBIT", net_premium_per_share: -0.30, downside_floor_pct: "-10.0%", upside_cap_pct: "+15.0%",
                collar_ticket: "COLLAR MSFT x1: BUY 485.0 PUT / SELL 620.0 CALL EXP ~9M (DEBIT $0.30/sh)",
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=MSFT"
            }
        ]);
    }

    function renderCollarsTable(collars) {
        const tbody = document.getElementById('collars-table-body');
        if (!tbody) return;

        tbody.innerHTML = '';
        collars.forEach(c => {
            const tr = document.createElement('tr');
            const isCredit = c.net_type === 'CREDIT';

            tr.innerHTML = `
                <td><strong class="mono" style="color: #60a5fa;">${c.symbol}</strong></td>
                <td style="text-align: right;" class="mono">$${c.spot.toFixed(2)}</td>
                <td style="text-align: center;" class="mono">${c.contracts} (${c.shares_protected} sh)</td>
                <td style="text-align: center;" class="mono">${c.dte}d</td>
                <td style="text-align: right;" class="mono">\$${c.put_strike.toFixed(1)} (\$${c.put_price.toFixed(2)})</td>
                <td style="text-align: right;" class="mono">\$${c.call_strike.toFixed(1)} (\$${c.call_price.toFixed(2)})</td>
                <td style="text-align: center;">
                    <span class="badge ${isCredit ? 'badge-strike' : 'badge-watchlist'}">
                        ${isCredit ? 'CREDIT' : 'DEBIT'} \$${Math.abs(c.net_premium_per_share).toFixed(2)}
                    </span>
                </td>
                <td style="text-align: center;" class="mono" style="color: #34d399;">${c.downside_floor_pct}</td>
                <td style="text-align: center;" class="mono" style="color: #60a5fa;">${c.upside_cap_pct}</td>
                <td style="font-family: var(--font-mono); font-size: 0.75rem; color: #38bdf8;">
                    ${c.collar_ticket}
                </td>
                <td style="text-align: center;">
                    <a href="${c.fidelity_deeplink}" target="_blank" class="btn-action btn-secondary" style="padding: 3px 8px; font-size: 0.74rem;">
                        Trade ↗
                    </a>
                </td>
            `;

            tbody.appendChild(tr);
        });
    }

    // --------------------------------------------------------------------------
    // 12. SEED FALLBACK DATA
    // --------------------------------------------------------------------------
    function loadSeedCandidates() {
        const seed = [
            {
                ticker: "GOOGL", name: "Alphabet Inc.", sector: "Communication Services", theme: "Search & Cloud Moat",
                spot: 248.50, sales5y: 15.2, roic: 33.4, pe: 23.8, pb: 6.8, de: 11.2, nd_ebitda: -0.70,
                raw_m: 7.42, clean_m: 7.42, insider_mult: 1.15, conviction_m: 8.53,
                all_med: true, is_strike_zone: true, is_trap: false,
                insider_notes: "Cluster Buying Boost (1.15x): 2 insiders bought $480k on open market.",
                insider_buys_usd: 480000, insider_sales_usd: 0,
                radar: { growth: 72, roic: 90, valuation: 74, solvency: 96 },
                pillar_pass: { growth: true, roic: true, pe: true, de: true }
            },
            {
                ticker: "FTNT", name: "Fortinet Inc.", sector: "Technology", theme: "Cybersecurity Cash Compounder",
                spot: 186.46, sales5y: 25.6, roic: 117.4, pe: 36.8, pb: 91.27, de: 24.1, nd_ebitda: -0.85,
                raw_m: 4.33, clean_m: 10.24, insider_mult: 0.70, conviction_m: 7.17,
                all_med: false, is_strike_zone: false, is_trap: false,
                insider_notes: "C-suite open-market distribution flagged (0.70x haircut).",
                insider_buys_usd: 0, insider_sales_usd: 24500000,
                radar: { growth: 88, roic: 99, valuation: 55, solvency: 92 },
                pillar_pass: { growth: true, roic: true, pe: false, de: true }
            },
            {
                ticker: "NVDA", name: "NVIDIA Corp", sector: "Technology", theme: "Accelerated Computing",
                spot: 214.13, sales5y: 65.2, roic: 68.4, pe: 42.1, pb: 28.5, de: 18.2, nd_ebitda: -0.12,
                raw_m: 18.36, clean_m: 18.36, insider_mult: 1.00, conviction_m: 18.36,
                all_med: false, is_strike_zone: false, is_trap: false,
                insider_notes: "Neutral open-market insider profile.",
                insider_buys_usd: 0, insider_sales_usd: 550219128,
                radar: { growth: 99, roic: 95, valuation: 48, solvency: 94 },
                pillar_pass: { growth: true, roic: true, pe: false, de: true }
            },
            {
                ticker: "LLY", name: "Eli Lilly and Co.", sector: "Healthcare", theme: "Biopharma Moat",
                spot: 1208.25, sales5y: 47.7, roic: 102.3, pe: 54.2, pb: 31.11, de: 112.5, nd_ebitda: 1.45,
                raw_m: 2.98, clean_m: 10.70, insider_mult: 1.00, conviction_m: 10.70,
                all_med: false, is_strike_zone: false, is_trap: false,
                insider_notes: "Neutral open-market insider profile.",
                insider_buys_usd: 0, insider_sales_usd: 7638150,
                radar: { growth: 95, roic: 98, valuation: 40, solvency: 85 },
                pillar_pass: { growth: true, roic: true, pe: false, de: true }
            },
            {
                ticker: "AAPL", name: "Apple Inc.", sector: "Technology", theme: "Consumer Moat",
                spot: 345.85, sales5y: 16.4, roic: 148.8, pe: 39.1, pb: 46.33, de: 78.4, nd_ebitda: 0.13,
                raw_m: 4.26, clean_m: 7.16, insider_mult: 0.70, conviction_m: 5.01,
                all_med: false, is_strike_zone: false, is_trap: false,
                insider_notes: "C-suite open-market sales flagged.",
                insider_buys_usd: 0, insider_sales_usd: 48000000,
                radar: { growth: 60, roic: 100, valuation: 45, solvency: 75 },
                pillar_pass: { growth: true, roic: true, pe: false, de: false }
            },
            {
                ticker: "MU", name: "Micron Technology", sector: "Technology", theme: "Memory Cyclical",
                spot: 939.53, sales5y: 36.8, roic: 8.2, pe: 14.5, pb: 2.8, de: 32.1,
                raw_m: 5.82, clean_m: 0.00, insider_mult: 1.00, conviction_m: 0.00,
                all_med: false, is_strike_zone: false, is_trap: true,
                insider_notes: "Blocked boom-bust memory commodity cyclical.",
                insider_buys_usd: 0, insider_sales_usd: 182280000,
                radar: { growth: 80, roic: 25, valuation: 85, solvency: 80 },
                pillar_pass: { growth: true, roic: false, pe: true, de: true }
            },
            {
                ticker: "COF", name: "Capital One Financial", sector: "Financial Services", theme: "Consumer Credit",
                spot: 154.20, sales5y: 12.4, roic: 18.2, pe: 11.2, pb: 1.15, de: 88.0,
                raw_m: 11.64, clean_m: 11.64, insider_mult: 1.00, conviction_m: 11.64,
                all_med: false, is_strike_zone: false, is_trap: false,
                insider_notes: "Neutral open-market insider profile.",
                insider_buys_usd: 0, insider_sales_usd: 0,
                radar: { growth: 55, roic: 65, valuation: 92, solvency: 68 },
                pillar_pass: { growth: false, roic: false, pe: true, de: false }
            }
        ];

        state.candidates = seed;
        updateKPICards({
            total_screened: 176,
            strike_zone_count: 3,
            cyclical_traps_count: 12,
            cluster_buys_count: 0,
            csuite_haircuts_count: 2
        });
        renderScreenerTable();
    }

    function loadSeedFidelityLots() {
        const seedLots = [
            {
                lot_id: 1, symbol: "AAPL", shares: 100.0, cost_basis: 18000.0,
                live_price: 345.85, live_value: 34585.0, unrealized_gain: 16585.0,
                unrealized_gain_pct: 92.14, days_held: 486, days_to_ltcg: 0,
                ltcg_status: "LTCG", action: "SELL (MOC)",
                rationale: "Sub-hurdle score (M=5.01). Position is LTCG (486d held). Reallocate capital to Strike Zone.",
                est_tax_savings: 0.0,
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=AAPL"
            },
            {
                lot_id: 2, symbol: "MSFT", shares: 50.0, cost_basis: 20000.0,
                live_price: 539.11, live_value: 26955.5, unrealized_gain: 6955.5,
                unrealized_gain_pct: 34.78, days_held: 279, days_to_ltcg: 86,
                ltcg_status: "STCG", action: "HOLD FOR TAX DEFERRAL (86d to LTCG)",
                rationale: "Sub-hurdle score (M=2.03). Defer exit 86 days to avoid ordinary income tax rate on $6,955 gain.",
                est_tax_savings: 1446.74,
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=MSFT"
            },
            {
                lot_id: 3, symbol: "MU", shares: 150.0, cost_basis: 10500.0,
                live_price: 939.53, live_value: 140929.5, unrealized_gain: 130429.5,
                unrealized_gain_pct: 1242.19, days_held: 76, days_to_ltcg: 289,
                ltcg_status: "STCG", action: "HOLD FOR TAX DEFERRAL (289d to LTCG)",
                rationale: "Sub-hurdle score (M=0.00). Defer exit 289 days to avoid ordinary income tax rate on $130,429 gain.",
                est_tax_savings: 27129.34,
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=MU"
            },
            {
                lot_id: 4, symbol: "INTC", shares: 200.0, cost_basis: 6000.0,
                live_price: 21.50, live_value: 4300.0, unrealized_gain: -1700.0,
                unrealized_gain_pct: -28.33, days_held: 215, days_to_ltcg: 150,
                ltcg_status: "STCG", action: "SELL (MOC)",
                rationale: "Immediate tax-loss harvest. Sub-hurdle score (M=0.85). Realize $1,700 loss to offset gains; redeploy.",
                est_tax_savings: 0.0,
                fidelity_deeplink: "https://digital.fidelity.com/ftgw/digital/trade-equity/index?symbol=INTC"
            }
        ];

        state.lots = seedLots;
        renderFidelityLots({
            summary: {
                total_value: 206770.0,
                total_basis: 54500.0,
                total_gain: 152270.0,
                total_gain_pct: 279.39,
                potential_tax_savings: 28576.08
            },
            lots: seedLots
        });
    }

    // --------------------------------------------------------------------------
    // 13. INITIALIZATION
    // --------------------------------------------------------------------------
    fetchStatus();
    loadSeedCandidates();
    loadSeedFidelityLots();
    pollWatcherStatus();

    // Auto-trigger live scan and background watcher poller if served over http
    if (window.location.protocol.startsWith('http')) {
        triggerScan();
        setInterval(() => {
            pollWatcherStatus(false);
        }, 3500);
    }
});
