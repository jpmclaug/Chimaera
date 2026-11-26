    // State Store
    let allItems = [];
    let currentView = 'grid';
    let currentQuickFilter = 'all';
    let historyChart = null;

    document.addEventListener("DOMContentLoaded", () => {
        loadItems();
    });

    function renderMicrocenterRecentScans(recentTimes) {
        const container = document.getElementById("mc-recent-scans-container");
        if (!container) return;
        if (!recentTimes || recentTimes.length === 0) {
            container.innerHTML = `<span class="text-[#94A3B8] italic">Awaiting initial sync</span>`;
            return;
        }
        container.innerHTML = recentTimes.slice(0, 3).map((t, idx) => {
            const isFirst = idx === 0;
            const borderClass = isFirst ? "border-[#00CED1]/50 text-white font-bold" : "border-[#263245] text-[#94A3B8]";
            const badgeClass = isFirst ? "text-[#00CED1]" : "text-[#64748B]";
            return `<span class="inline-flex items-center space-x-1 bg-[#1B2230] border ${borderClass} px-2 py-0.5 text-[10px]">
                <span class="${badgeClass} font-bold">#${idx + 1}:</span>
                <span>${formatEST(t)}</span>
            </span>`;
        }).join("");
    }

    async function loadItems() {
        const loadingEl = document.getElementById("mc-loading");
        const emptyEl = document.getElementById("mc-empty-state");
        const gridEl = document.getElementById("mc-grid-container");
        const tableEl = document.getElementById("mc-table-container");

        try {
            const resp = await fetch("/api/microcenter/items");
            const data = await resp.json();
            allItems = data.items || [];

            // Update stats
            document.getElementById("stat-total-items").innerText = data.total_tracked || allItems.length;
            document.getElementById("stat-in-stock-items").innerText = data.in_stock_tracked || allItems.filter(i => i.in_stock).length;
            document.getElementById("stat-deals-items").innerText = data.deals_tracked || allItems.filter(i => i.is_deal).length;
            if (data.last_scan_time) {
                const legacyLabel = document.getElementById("mc-last-scan-label");
                if (legacyLabel) legacyLabel.innerText = formatEST(data.last_scan_time);
            }
            if (data.recent_scan_times && data.recent_scan_times.length > 0) {
                renderMicrocenterRecentScans(data.recent_scan_times);
            } else if (data.last_scan_time) {
                renderMicrocenterRecentScans([data.last_scan_time]);
            }

            applyFilters();
        } catch (err) {
            console.error("Error loading MicroCenter items:", err);
            showToast("Failed to load MicroCenter items", "error");
        }
    }

    function applyFilters() {
        const query = (document.getElementById("mc-search").value || "").toLowerCase().trim();
        const statusFilter = document.getElementById("mc-filter-status").value;
        const sortBy = document.getElementById("mc-sort-by").value;

        let filtered = [...allItems];

        // Search
        if (query) {
            filtered = filtered.filter(i => {
                const nameMatch = (i.display_name || i.name || "").toLowerCase().includes(query);
                const skuMatch = (i.sku || "").toLowerCase().includes(query);
                return nameMatch || skuMatch;
            });
        }

        // Status Filter
        if (statusFilter === "in_stock") {
            filtered = filtered.filter(i => i.in_stock);
        } else if (statusFilter === "deals") {
            filtered = filtered.filter(i => i.is_deal || i.has_price_dropped);
        } else if (statusFilter === "low_stock") {
            filtered = filtered.filter(i => i.in_stock && i.stock_count !== null && i.stock_count <= 5 && i.stock_count > 0);
        } else if (statusFilter === "out_of_stock") {
            filtered = filtered.filter(i => !i.in_stock);
        }

        // Quick Filter override if set
        if (currentQuickFilter !== 'all') {
            if (currentQuickFilter === "in_stock") filtered = filtered.filter(i => i.in_stock);
            if (currentQuickFilter === "deals") filtered = filtered.filter(i => i.is_deal || i.has_price_dropped);
            if (currentQuickFilter === "low_stock") filtered = filtered.filter(i => i.in_stock && i.stock_count !== null && i.stock_count <= 5 && i.stock_count > 0);
        }

        // Sorting
        if (sortBy === "price_asc") {
            filtered.sort((a, b) => a.current_price - b.current_price);
        } else if (sortBy === "price_desc") {
            filtered.sort((a, b) => b.current_price - a.current_price);
        } else if (sortBy === "stock_desc") {
            filtered.sort((a, b) => (b.in_stock ? (b.stock_count || 100) : -1) - (a.in_stock ? (a.stock_count || 100) : -1));
        } else if (sortBy === "price_drop") {
            filtered.sort((a, b) => (a.price_change_amount || 0) - (b.price_change_amount || 0));
        } else if (sortBy === "name") {
            filtered.sort((a, b) => (a.display_name || a.name).localeCompare(b.display_name || b.name));
        } else {
            // Default: Deals & in-stock first
            filtered.sort((a, b) => {
                if (a.is_deal !== b.is_deal) return a.is_deal ? -1 : 1;
                if (a.in_stock !== b.in_stock) return a.in_stock ? -1 : 1;
                return (a.display_name || a.name).localeCompare(b.display_name || b.name);
            });
        }

        // Update counter
        document.getElementById("mc-counter-val").innerText = filtered.length;

        // Render
        renderGrid(filtered);
        renderTable(filtered);

        const emptyEl = document.getElementById("mc-empty-state");
        if (filtered.length === 0) {
            emptyEl.classList.remove("hidden");
        } else {
            emptyEl.classList.add("hidden");
        }
    }

    function renderGrid(items) {
        const grid = document.getElementById("mc-grid-container");
        grid.innerHTML = items.map(item => {
            const hasDrop = item.has_price_dropped || item.price_change_amount < 0;
            const priceDeltaText = item.price_change_amount ? (item.price_change_amount < 0 ? `-${Math.abs(item.price_change_amount).toFixed(2)}` : `+${item.price_change_amount.toFixed(2)}`) : null;
            const displayName = item.display_name || item.name;

            // Stock pill formatting
            let stockBadge = `<span class="px-2 py-0.5 text-[10px] font-mono font-bold bg-[#DC143C]/20 border border-[#DC143C]/50 text-[#FF3358]">✗ OUT OF STOCK</span>`;
            if (item.in_stock) {
                if (item.stock_count !== null && item.stock_count <= 5) {
                    stockBadge = `<span class="px-2 py-0.5 text-[10px] font-mono font-bold bg-[#F59E0B]/20 border border-[#F59E0B]/50 text-[#F59E0B]">⚠️ ${item.stock_count} IN STOCK (LOW)</span>`;
                } else {
                    const countTxt = item.stock_count ? `${item.stock_count}+ IN STOCK` : 'IN STOCK';
                    stockBadge = `<span class="px-2 py-0.5 text-[10px] font-mono font-bold bg-[#00CED1]/20 border border-[#00CED1]/50 text-[#00CED1]">✓ ${countTxt}</span>`;
                }
            }

            // Day over day info
            const dod = item.day_over_day;
            let dodHtml = `<span class="text-[10px] text-[#64748B]">Day/Day: Stable</span>`;
            if (dod && dod.has_baseline && (dod.price_delta !== 0 || dod.stock_delta !== 0)) {
                const pDelta = dod.price_delta < 0 ? `<span class="text-[#2DD4BF] font-bold">${dod.price_delta.toFixed(2)}</span>` : (dod.price_delta > 0 ? `<span class="text-[#FF3358] font-bold">+${dod.price_delta.toFixed(2)}</span>` : `<span class="text-[#94A3B8]">$0.00</span>`);
                const sDelta = dod.stock_delta !== 0 ? `<span class="text-white ml-1.5">Stock: ${dod.stock_delta > 0 ? '+' : ''}${dod.stock_delta}</span>` : '';
                dodHtml = `<div class="text-[10px] font-mono"><span class="text-[#94A3B8]">D/D:</span> ${pDelta} ${sDelta}</div>`;
            }

            return `
            <div class="card-tactical flex flex-col justify-between p-3.5 space-y-3 ${item.is_deal ? 'border-[#DC143C] shadow-lg shadow-[#DC143C]/5' : ''}">
                <!-- Top Header / Badges -->
                <div>
                    <div class="flex items-center justify-between gap-1 mb-2">
                        <span class="text-[9px] font-mono uppercase text-[#94A3B8] tracking-wider truncate">SKU: <strong class="text-white">${item.sku}</strong></span>
                        <div class="flex items-center space-x-1 flex-shrink-0">
                            ${item.is_deal ? '<span class="bg-[#DC143C] text-white text-[9px] font-mono font-bold px-1.5 py-0.2 uppercase">🔥 MARKDOWN</span>' : ''}
                            ${stockBadge}
                        </div>
                    </div>

                    <!-- Clean Product Title -->
                    <div class="min-w-0">
                        <a href="${item.product_url || '#'}" target="_blank" rel="noopener noreferrer" class="text-sm font-bold text-white hover:text-[#F59E0B] transition line-clamp-2 leading-snug">
                            ${displayName}
                        </a>
                        <div class="text-[10px] text-[#64748B] mt-1 font-mono flex items-center space-x-1">
                            <span>📍 Store #175 &bull; Charlotte</span>
                        </div>
                    </div>
                </div>

                <!-- Price & Day-over-Day Section -->
                <div class="bg-[#10141D] p-2.5 border border-[#263245] space-y-1.5">
                    <div class="flex items-baseline justify-between">
                        <div>
                            <span class="text-[10px] text-[#94A3B8] uppercase font-mono mr-1">PRICE:</span>
                            <span class="text-base font-mono font-bold ${hasDrop ? 'text-[#2DD4BF]' : 'text-white'}">$${item.current_price.toFixed(2)}</span>
                            ${item.original_price && item.original_price > item.current_price ? `<span class="text-[10px] text-[#64748B] line-through ml-1">$${item.original_price.toFixed(2)}</span>` : ''}
                        </div>
                        ${hasDrop ? `<span class="text-[10px] font-mono font-bold text-[#2DD4BF] bg-[#2DD4BF]/10 px-1 py-0.2 border border-[#2DD4BF]/30">${item.price_change_percent.toFixed(1)}%</span>` : ''}
                    </div>
                    <div class="border-t border-[#263245]/60 pt-1 flex items-center justify-between">
                        ${dodHtml}
                        ${item.target_price ? `<span class="text-[9px] font-mono text-[#FF3358]" title="Target Acquisition Threshold">Target: $${item.target_price.toFixed(2)}</span>` : ''}
                    </div>
                </div>

                <!-- Action Controls: Trends & Alerts -->
                <div class="grid grid-cols-2 gap-2 pt-1">
                    <button onclick="openHistoryModal(${item.id})" class="btn-bridge col-span-1 py-1.5 text-xs font-mono text-center flex items-center justify-center space-x-1.5" title="View Day-over-Day Price & Stock Trends">
                        <svg class="w-3.5 h-3.5 text-[#00CED1]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="square" stroke-linejoin="miter" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path>
                        </svg>
                        <span>Trends</span>
                    </button>
                    <button onclick="openAlertModal(${item.id})" class="btn-bridge col-span-1 py-1.5 text-xs font-mono text-center flex items-center justify-center space-x-1.5" title="Configure Alert Thresholds">
                        <svg class="w-3.5 h-3.5 text-[#FF3358]" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="square" stroke-linejoin="miter" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"></path>
                        </svg>
                        <span>Alerts</span>
                    </button>
                </div>
            </div>
            `;
        }).join("");
    }

    function renderTable(items) {
        const tbody = document.getElementById("mc-table-body");
        tbody.innerHTML = items.map(item => {
            const hasDrop = item.has_price_dropped || item.price_change_amount < 0;
            const dod = item.day_over_day;
            const pDelta = (dod && dod.has_baseline && dod.price_delta !== 0) ? (dod.price_delta < 0 ? `<span class="text-[#2DD4BF]">${dod.price_delta.toFixed(2)}</span>` : `<span class="text-[#FF3358]">+${dod.price_delta.toFixed(2)}</span>`) : `<span class="text-[#64748B]">$0.00</span>`;
            const displayName = item.display_name || item.name;

            let stockHtml = `<span class="text-[#FF3358] font-bold">✗ Out of Stock</span>`;
            if (item.in_stock) {
                if (item.stock_count !== null && item.stock_count <= 5) {
                    stockHtml = `<span class="text-[#F59E0B] font-bold">⚠️ ${item.stock_count} In Stock</span>`;
                } else {
                    stockHtml = `<span class="text-[#00CED1] font-bold">✓ ${item.stock_count ? item.stock_count + ' In Stock' : 'In Stock'}</span>`;
                }
            }

            return `
            <tr class="hover:bg-[#10141D] transition">
                <td class="p-3">
                    <div class="min-w-0">
                        <a href="${item.product_url || '#'}" target="_blank" class="text-white hover:text-[#F59E0B] font-bold truncate block max-w-md">
                            ${displayName}
                        </a>
                    </div>
                </td>
                <td class="p-3 text-[#94A3B8]">${item.sku}</td>
                <td class="p-3 text-white font-bold">$${item.current_price.toFixed(2)}</td>
                <td class="p-3 text-[#94A3B8]">
                    ${item.original_price ? `<span class="line-through">$${item.original_price.toFixed(2)}</span>` : (item.previous_price ? `$${item.previous_price.toFixed(2)}` : '-')}
                </td>
                <td class="p-3">${pDelta}</td>
                <td class="p-3">${stockHtml}</td>
                <td class="p-3 text-right">
                    <div class="flex items-center justify-end space-x-2">
                        <button onclick="openHistoryModal(${item.id})" class="btn-bridge px-2.5 py-1 text-xs text-[#00CED1]">Trends</button>
                        <button onclick="openAlertModal(${item.id})" class="btn-bridge px-2.5 py-1 text-xs text-[#FF3358]">Alerts</button>
                    </div>
                </td>
            </tr>
            `;
        }).join("");
    }

    function setMcView(mode) {
        currentView = mode;
        document.getElementById("btn-mc-view-grid").classList.toggle("active", mode === 'grid');
        document.getElementById("btn-mc-view-table").classList.toggle("active", mode === 'table');
        document.getElementById("btn-mc-view-feed").classList.toggle("active", mode === 'feed');

        document.getElementById("mc-grid-container").classList.toggle("hidden", mode !== 'grid');
        document.getElementById("mc-table-container").classList.toggle("hidden", mode !== 'table');
        document.getElementById("mc-feed-container").classList.toggle("hidden", mode !== 'feed');

        if (mode === 'feed') {
            loadActivityFeed();
        }
    }

    function setQuickFilter(mode) {
        currentQuickFilter = mode;
        document.querySelectorAll(".mc-quick-pill").forEach(p => {
            p.classList.toggle("active", p.dataset.filter === mode);
        });
        applyFilters();
    }

    async function triggerMicrocenterSync() {
        const btn = document.getElementById("btn-mc-sync");
        const spinner = document.getElementById("mc-sync-spinner");
        btn.disabled = true;
        spinner.classList.add("animate-spin");
        showToast("Initiating MicroCenter Charlotte store inventory sweep...", "info");

        try {
            const resp = await fetch("/api/microcenter/sync", { method: "POST" });
            const data = await resp.json();
            if (data.success) {
                showToast(data.message || "MicroCenter sync completed successfully!", "success");
                await loadItems();
            } else {
                showToast(data.error || data.message || "Sync encountered an error", "error");
            }
        } catch (err) {
            console.error("Sync error:", err);
            showToast("Failed to connect for MicroCenter sync", "error");
        } finally {
            btn.disabled = false;
            spinner.classList.remove("animate-spin");
        }
    }

    // History Modal & Chart.js Integration
    async function openHistoryModal(itemId) {
        const modal = document.getElementById("history-modal");
        modal.classList.remove("hidden");

        try {
            const resp = await fetch(`/api/microcenter/history/${itemId}`);
            const data = await resp.json();
            const item = data.item;
            const history = data.history || [];
            const dod = data.day_over_day || {};

            document.getElementById("hist-modal-title").innerText = item.display_name || item.name;
            document.getElementById("hist-modal-sku").innerText = `SKU: ${item.sku} | Store: MicroCenter ${item.store_name} (#${item.store_id})`;
            document.getElementById("hist-stat-curr-price").innerText = `$${item.current_price.toFixed(2)}`;
            
            // DOD Price
            const dodPriceEl = document.getElementById("hist-stat-dod-price");
            if (dod.has_baseline && dod.price_delta !== 0) {
                dodPriceEl.innerText = `${dod.price_delta < 0 ? '-' : '+'}$${Math.abs(dod.price_delta).toFixed(2)} (${dod.price_delta_percent > 0 ? '+' : ''}${dod.price_delta_percent}%)`;
                dodPriceEl.className = dod.price_delta < 0 ? "text-sm sm:text-base font-bold text-[#2DD4BF]" : "text-sm sm:text-base font-bold text-[#FF3358]";
            } else {
                dodPriceEl.innerText = "$0.00 (Stable)";
                dodPriceEl.className = "text-sm sm:text-base font-bold text-[#94A3B8]";
            }

            // DOD Stock
            document.getElementById("hist-stat-stock").innerText = item.in_stock ? (item.stock_count ? `${item.stock_count} In Stock` : 'In Stock') : 'Out of Stock';
            const dodStockEl = document.getElementById("hist-stat-dod-stock");
            if (dod.has_baseline && dod.stock_delta !== 0) {
                dodStockEl.innerText = `${dod.stock_delta > 0 ? '+' : ''}${dod.stock_delta} Units`;
                dodStockEl.className = dod.stock_delta > 0 ? "text-sm sm:text-base font-bold text-[#00CED1]" : "text-sm sm:text-base font-bold text-[#FF3358]";
            } else {
                dodStockEl.innerText = "0 (Unchanged)";
                dodStockEl.className = "text-sm sm:text-base font-bold text-[#94A3B8]";
            }

            // Render Table Rows
            const tbody = document.getElementById("hist-table-body");
            tbody.innerHTML = history.map(h => {
                const dateStr = formatEST(h.recorded_at, false);
                const pDelta = h.price_change !== 0 ? (h.price_change < 0 ? `<span class="text-[#2DD4BF]">${h.price_change.toFixed(2)}</span>` : `<span class="text-[#FF3358]">+${h.price_change.toFixed(2)}</span>`) : `<span class="text-[#64748B]">0.00</span>`;
                const sDelta = h.stock_change !== 0 ? (h.stock_change > 0 ? `<span class="text-[#00CED1]">+${h.stock_change}</span>` : `<span class="text-[#FF3358]">${h.stock_change}</span>`) : `<span class="text-[#64748B]">0</span>`;
                return `
                <tr class="hover:bg-[#1B2230]">
                    <td class="p-2 text-[#94A3B8] font-mono">${dateStr}</td>
                    <td class="p-2 font-bold text-white font-mono">$${h.price.toFixed(2)}</td>
                    <td class="p-2 font-mono">${pDelta}</td>
                    <td class="p-2 font-mono ${h.in_stock ? 'text-[#00CED1]' : 'text-[#FF3358]'}">${h.in_stock ? (h.stock_count || 'In Stock') : 'Out'}</td>
                    <td class="p-2 font-mono">${sDelta}</td>
                </tr>
                `;
            }).join("");

            // Render Chart
            renderHistoryChart(history, item);

        } catch (err) {
            console.error("Error opening history modal:", err);
            showToast("Failed to load historical timeline", "error");
        }
    }

    function renderHistoryChart(history, item) {
        const ctx = document.getElementById("historyChartCanvas").getContext("2d");
        if (historyChart) {
            historyChart.destroy();
        }

        const labels = history.map(h => h.recorded_at ? h.recorded_at.replace("T", " ").slice(5, 16) : '');
        const prices = history.map(h => h.price);
        const stocks = history.map(h => h.stock_count !== null ? h.stock_count : (h.in_stock ? 25 : 0));

        historyChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Price ($ USD)',
                        data: prices,
                        borderColor: '#00CED1',
                        backgroundColor: 'rgba(0, 206, 209, 0.1)',
                        borderWidth: 2,
                        yAxisID: 'yPrice',
                        stepped: true,
                        tension: 0,
                    },
                    {
                        label: 'Charlotte Stock Count',
                        data: stocks,
                        borderColor: '#F59E0B',
                        backgroundColor: 'rgba(245, 158, 11, 0.15)',
                        borderWidth: 2,
                        yAxisID: 'yStock',
                        type: 'bar',
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                    x: {
                        ticks: { color: '#94A3B8', font: { family: 'monospace', size: 10 } },
                        grid: { color: 'rgba(38, 50, 69, 0.6)' }
                    },
                    yPrice: {
                        type: 'linear',
                        position: 'left',
                        ticks: { color: '#00CED1', font: { family: 'monospace', size: 10 }, callback: v => '$' + v },
                        grid: { color: 'rgba(38, 50, 69, 0.6)' }
                    },
                    yStock: {
                        type: 'linear',
                        position: 'right',
                        ticks: { color: '#F59E0B', font: { family: 'monospace', size: 10 } },
                        grid: { drawOnChartArea: false }
                    }
                },
                plugins: {
                    legend: {
                        labels: { color: '#F1F5F9', font: { family: 'monospace', size: 11 } }
                    }
                }
            }
        });
    }

    function closeHistoryModal() {
        document.getElementById("history-modal").classList.add("hidden");
    }

    // Alert Preferences Modal
    function openAlertModal(itemId) {
        const item = allItems.find(i => i.id === itemId);
        if (!item) return;

        document.getElementById("alert-item-id").value = item.id;
        document.getElementById("alert-modal-name").innerText = item.display_name || item.name;
        document.getElementById("alert-target-price").value = item.target_price || '';
        document.getElementById("alert-notify-price").checked = item.notify_on_price_change !== false;
        document.getElementById("alert-notify-restock").checked = item.notify_on_restock !== false;
        document.getElementById("alert-notify-low-stock").checked = item.notify_on_low_stock !== false;

        document.getElementById("alert-modal").classList.remove("hidden");
    }

    function closeAlertModal() {
        document.getElementById("alert-modal").classList.add("hidden");
    }

    async function saveAlertPreferences(e) {
        e.preventDefault();
        const itemId = document.getElementById("alert-item-id").value;
        const targetPrice = document.getElementById("alert-target-price").value;
        const notifyPrice = document.getElementById("alert-notify-price").checked;
        const notifyRestock = document.getElementById("alert-notify-restock").checked;
        const notifyLowStock = document.getElementById("alert-notify-low-stock").checked;

        try {
            const resp = await fetch(`/api/microcenter/item/${itemId}/update`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    target_price: targetPrice,
                    notify_on_price_change: notifyPrice,
                    notify_on_restock: notifyRestock,
                    notify_on_low_stock: notifyLowStock,
                })
            });
            const data = await resp.json();
            if (data.success) {
                showToast("Alert preferences updated successfully!", "success");
                closeAlertModal();
                await loadItems();
            } else {
                showToast(data.error || "Failed to update alert settings", "error");
            }
        } catch (err) {
            console.error("Error saving alert preferences:", err);
            showToast("Failed to save alert preferences", "error");
        }
    }

    // Activity Feed
    async function loadActivityFeed() {
        const feedList = document.getElementById("mc-feed-list");
        feedList.innerHTML = `<div class="text-xs text-[#94A3B8] font-mono py-4 text-center">Loading activity feed...</div>`;

        try {
            const resp = await fetch("/api/microcenter/changes");
            const data = await resp.json();
            const events = data.events || [];

            if (events.length === 0) {
                feedList.innerHTML = `<div class="text-xs text-[#64748B] font-mono py-6 text-center">No recent price or stock changes recorded yet. Changes will be logged across daily surveillance cycles.</div>`;
                return;
            }

            feedList.innerHTML = events.map(ev => {
                const isDrop = ev.price_change < 0;
                const isRestock = ev.stock_change > 0;
                const dateStr = formatEST(ev.recorded_at, false);
                return `
                <div class="p-3 bg-[#10141D] border border-[#263245] flex items-center justify-between font-mono text-xs">
                    <div class="min-w-0">
                        <div class="text-white font-bold truncate">${ev.name}</div>
                        <div class="text-[10px] text-[#94A3B8]">SKU: ${ev.sku} &bull; ${dateStr}</div>
                    </div>
                    <div class="text-right flex-shrink-0 ml-3">
                        ${ev.price_change !== 0 ? `
                            <div class="${isDrop ? 'text-[#2DD4BF]' : 'text-[#FF3358]'} font-bold">
                                ${isDrop ? 'Price Drop' : 'Price Rise'}: ${ev.price_change < 0 ? '-' : '+'}$${Math.abs(ev.price_change).toFixed(2)} &rarr; $${ev.price.toFixed(2)}
                            </div>
                        ` : ''}
                        ${ev.stock_change !== 0 ? `
                            <div class="${isRestock ? 'text-[#00CED1]' : 'text-[#94A3B8]'} text-[11px]">
                                Stock ${ev.stock_change > 0 ? '+' : ''}${ev.stock_change} &bull; ${ev.stock_count || 0} In Stock
                            </div>
                        ` : ''}
                    </div>
                </div>
                `;
            }).join("");
        } catch (err) {
            console.error("Error loading activity feed:", err);
            feedList.innerHTML = `<div class="text-xs text-[#FF3358] font-mono py-4 text-center">Failed to load activity feed.</div>`;
        }
    }

    function formatEST(dateInput, includeSeconds = true) {
        if (!dateInput) return '--';
        try {
            const d = (dateInput instanceof Date) ? dateInput : new Date(dateInput);
            if (isNaN(d.getTime())) return String(dateInput).replace('T', ' ').slice(0, 19) + ' EST';
            const options = {
                timeZone: 'America/New_York',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                ...(includeSeconds ? { second: '2-digit' } : {}),
                hour12: false
            };
            return new Intl.DateTimeFormat('en-US', options).format(d) + ' EST';
        } catch {
            return String(dateInput);
        }
    }
