// =========================================================================
// Black Friday TCGplayer Seller Scout & Combined Sealed+Singles Carts
// =========================================================================
let sellerScoutInitialized = false;
let currentScoutSubmode = "value_scan";
let savedTcgSellers = [];
let currentSellerScanResult = null;
let currentCombinedCartsResult = null;
let scoutItemRegistry = {}; // itemKey -> item object
let selectedScoutKeys = new Set();
const scoutBucketCollapsed = {
    watchlist: false,
    deck_upgrade: false,
    value: false,
    sealed: false,
};

function _scoutEsc(val) {
    if (typeof escapeHtml === "function") {
        return escapeHtml(val == null ? "" : String(val));
    }
    return String(val == null ? "" : val)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function _scoutUsd(val) {
    if (val == null || isNaN(Number(val))) return "N/A";
    return "$" + Number(val).toFixed(2);
}

function _scoutToast(msg, level) {
    if (typeof showToast === "function") {
        showToast(msg, level || "info");
    }
}

function initSellerScout() {
    if (sellerScoutInitialized) return;
    sellerScoutInitialized = true;

    const jsonEl = document.getElementById("saved-tcg-sellers-json");
    if (jsonEl) {
        try {
            const parsed = JSON.parse(jsonEl.textContent || "[]");
            if (Array.isArray(parsed)) savedTcgSellers = parsed;
        } catch (err) {
            console.warn("Could not parse saved-tcg-sellers-json:", err);
        }
    }
    renderSavedSellerChips();

    const sellerInput = document.getElementById("scout-seller-input");
    if (sellerInput) {
        sellerInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                runSellerValueScan(false);
            }
        });
    }
}

function setScoutSubmode(mode) {
    currentScoutSubmode = mode === "combined_carts" ? "combined_carts" : "value_scan";
    const valPanel = document.getElementById("scout-subpanel-value-scan");
    const combPanel = document.getElementById("scout-subpanel-combined-carts");
    const btnVal = document.getElementById("btn-scout-submode-value");
    const btnComb = document.getElementById("btn-scout-submode-combined");

    if (valPanel && combPanel) {
        if (currentScoutSubmode === "combined_carts") {
            valPanel.classList.add("hidden");
            combPanel.classList.remove("hidden");
        } else {
            combPanel.classList.add("hidden");
            valPanel.classList.remove("hidden");
        }
    }

    if (btnVal && btnComb) {
        const baseCls = "scout-submode-btn p-2.5 sm:p-3 text-left border border-[#263245] bg-[#10141D] text-[#94A3B8] hover:text-white transition";
        btnVal.className = currentScoutSubmode === "value_scan" ? baseCls + " active" : baseCls;
        btnComb.className = currentScoutSubmode === "combined_carts" ? baseCls + " active" : baseCls;
    }
}

function renderSavedSellerChips() {
    const wrap = document.getElementById("scout-saved-sellers-chips");
    const countEl = document.getElementById("scout-saved-count");
    if (countEl) countEl.textContent = String(savedTcgSellers.length);
    if (!wrap) return;

    if (!savedTcgSellers.length) {
        wrap.innerHTML = `<span class="text-[11px] text-[#64748B]">No saved sellers yet. Paste a TCGplayer store URL below and click ★ Save to pin recommended Black Friday shops.</span>`;
        return;
    }

    wrap.innerHTML = savedTcgSellers.map((s) => {
        const sk = _scoutEsc(s.sellerKey);
        const sname = _scoutEsc(s.sellerName || s.sellerKey);
        const noteHtml = s.note ? `<span class="text-[10px] text-[#94A3B8] ml-1">· ${_scoutEsc(s.note)}</span>` : "";
        const sum = s.lastSummary || {};
        const totalRecs = sum.totalRecommendations;
        const badgeHtml = typeof totalRecs === "number"
            ? `<span class="ml-1.5 px-1 py-0.2 bg-[#10B981]/20 text-[#10B981] text-[10px]">${totalRecs} picks</span>`
            : "";

        return `
            <div class="inline-flex items-center bg-[#10141D] border border-[#263245] hover:border-[#10B981] transition">
                <button type="button" onclick="scanSavedSeller('${sk}')"
                    class="px-2.5 py-1 text-xs font-mono text-white hover:text-[#10B981] flex items-center text-left"
                    title="Click to scan ${sname} (${sk})">
                    <span class="text-[#F59E0B] mr-1">★</span>
                    <span class="font-bold">${sname}</span>
                    ${noteHtml}
                    ${badgeHtml}
                </button>
                <button type="button" onclick="deleteSavedSeller(${Number(s.id)}, event)"
                    class="px-1.5 py-1 text-xs text-[#64748B] hover:text-[#FF3358] border-l border-[#263245]"
                    title="Remove saved seller">
                    &times;
                </button>
            </div>
        `;
    }).join("");
}

function scanSavedSeller(sellerKey) {
    setScoutSubmode("value_scan");
    const inp = document.getElementById("scout-seller-input");
    if (inp) inp.value = sellerKey;
    runSellerValueScan(false);
}

async function saveCurrentScoutSeller() {
    const inp = document.getElementById("scout-seller-input");
    const noteEl = document.getElementById("scout-seller-note");
    const rawSeller = (inp ? inp.value.trim() : "") || (currentSellerScanResult && currentSellerScanResult.seller && currentSellerScanResult.seller.sellerKey) || "";
    if (!rawSeller) {
        _scoutToast("Enter a TCGplayer store URL or seller key first.", "warning");
        return;
    }
    const note = noteEl ? noteEl.value.trim() : "";

    try {
        const resp = await fetch("/api/tcgplayer/saved-sellers", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ seller: rawSeller, note }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            _scoutToast(data.error || "Could not save seller.", "error");
            return;
        }
        if (Array.isArray(data.sellers)) {
            savedTcgSellers = data.sellers;
            renderSavedSellerChips();
        }
        _scoutToast(`Saved ${data.seller.sellerName} to favorite Black Friday sellers!`, "success");
    } catch (err) {
        console.error("Save seller failed:", err);
        _scoutToast("Network error while saving seller.", "error");
    }
}

async function deleteSavedSeller(savedId, ev) {
    if (ev) ev.stopPropagation();
    try {
        const resp = await fetch(`/api/tcgplayer/saved-sellers/${savedId}`, {
            method: "DELETE",
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            _scoutToast(data.error || "Failed to remove saved seller.", "error");
            return;
        }
        if (Array.isArray(data.sellers)) {
            savedTcgSellers = data.sellers;
            renderSavedSellerChips();
        }
        _scoutToast("Removed saved seller.", "info");
    } catch (err) {
        console.error("Delete saved seller error:", err);
        _scoutToast("Network error removing saved seller.", "error");
    }
}

async function runSellerValueScan(forceRefresh) {
    const inp = document.getElementById("scout-seller-input");
    const sellerVal = inp ? inp.value.trim() : "";
    if (!sellerVal) {
        _scoutToast("Paste a TCGplayer store URL (?seller=...) or 8-char seller key.", "warning");
        if (inp) inp.focus();
        return;
    }

    const btn = document.getElementById("btn-run-seller-scan");
    const btnLabel = document.getElementById("scout-scan-btn-label");
    const spinner = document.getElementById("scout-scan-spinner");
    const statusText = document.getElementById("scout-value-status-text");

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove("hidden");
    if (btnLabel) btnLabel.textContent = "SCANNING SELLER STORE...";
    if (statusText) statusText.textContent = "Scanning store inventory, watchlist, deck upgrades & sealed deals...";

    const conditions = [];
    if (document.getElementById("scout-cond-nm")?.checked) conditions.push("NM");
    if (document.getElementById("scout-cond-lp")?.checked) conditions.push("LP");
    if (!conditions.length) conditions.push("NM", "LP");

    const payload = {
        seller: sellerVal,
        min_discount_pct: parseFloat(document.getElementById("scout-min-discount")?.value || "10"),
        min_market: parseFloat(document.getElementById("scout-min-market")?.value || "1"),
        conditions,
        allow_mp: Boolean(document.getElementById("scout-allow-mp")?.checked),
        include_watchlist: Boolean(document.getElementById("scout-inc-watchlist")?.checked),
        include_deck_upgrades: Boolean(document.getElementById("scout-inc-decks")?.checked),
        include_value: Boolean(document.getElementById("scout-inc-value")?.checked),
        include_sealed: Boolean(document.getElementById("scout-inc-sealed")?.checked),
        exclude_owned: Boolean(document.getElementById("scout-exclude-owned")?.checked),
        english_only: Boolean(document.getElementById("scout-english-only")?.checked),
        save_seller: Boolean(document.getElementById("scout-save-on-scan")?.checked),
        note: (document.getElementById("scout-seller-note")?.value || "").trim(),
        force_refresh: Boolean(forceRefresh),
    };

    try {
        const resp = await fetch("/api/tcgplayer/seller/value-scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            _scoutToast(data.error || "Seller scan failed.", "error");
            if (statusText) statusText.textContent = data.error || "Scan failed.";
            return;
        }

        currentSellerScanResult = data;
        if (Array.isArray(data.savedSellers)) {
            savedTcgSellers = data.savedSellers;
            renderSavedSellerChips();
        }
        renderSellerValueResults(data);

        const sum = data.summary || {};
        const dur = data.storeStats ? data.storeStats.durationSeconds : 0;
        const cacheNote = data.storeStats && data.storeStats.fromCache ? " (cached)" : "";
        if (statusText) {
            statusText.textContent = `Scanned ${data.seller.sellerName} in ${dur}s${cacheNote}: ${sum.totalRecommendations || 0} recommendations.`;
        }
        _scoutToast(`Found ${sum.totalRecommendations || 0} picks at ${data.seller.sellerName}.`, "success");
    } catch (err) {
        console.error("Seller value scan failed:", err);
        _scoutToast("Network error during seller scan.", "error");
        if (statusText) statusText.textContent = "Network error during seller scan.";
    } finally {
        if (btn) btn.disabled = false;
        if (spinner) spinner.classList.add("hidden");
        if (btnLabel) btnLabel.textContent = "🔍 SCAN SELLER STORE";
    }
}

function _buildReasonBadgesHtml(item) {
    const badges = [];
    const reasons = item.reasons || [];
    if (reasons.includes("watchlist")) {
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#00CED1]/20 text-[#00CED1] border border-[#00CED1]/40 uppercase">WATCHLIST</span>`);
    }
    if (reasons.includes("watchlist_sealed")) {
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40 uppercase">WATCHED SEALED</span>`);
    }
    if (reasons.includes("deck_upgrade") && Array.isArray(item.decks)) {
        item.decks.slice(0, 2).forEach((d) => {
            const syn = d.synergy != null ? ` (${Math.round(d.synergy * 100)}%)` : "";
            badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#8B5CF6]/20 text-[#DDD6FE] border border-[#8B5CF6]/50 uppercase">DECK: ${_scoutEsc(d.deckName)}${syn}</span>`);
        });
        if (item.decks.length > 2) {
            badges.push(`<span class="px-1.5 py-0.5 text-[10px] bg-[#8B5CF6]/15 text-[#DDD6FE] border border-[#8B5CF6]/40">+${item.decks.length - 2} decks</span>`);
        }
    }
    if (reasons.includes("value") || reasons.includes("below_market")) {
        const disc = item.discountPct != null ? `-${Number(item.discountPct).toFixed(0)}%` : "BELOW MKT";
        const label = item.valueLabel === "Steal" ? "STEAL" : "VALUE";
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/50 uppercase">${label} ${disc}</span>`);
    }
    if (item.inBinder) {
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#334155] text-[#F1F5F9] uppercase">OWN &times;${Number(item.ownedCount || 1)}</span>`);
    }
    if (item.verifyFlag) {
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#F59E0B]/25 text-[#F59E0B] border border-[#F59E0B]/60 uppercase" title="Priced below 50% of market — verify condition/printing on TCGplayer">⚠ VERIFY</span>`);
    }
    if (item.foilUnverified) {
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#F59E0B]/15 text-[#F59E0B] border border-[#F59E0B]/40 uppercase">FOIL?</span>`);
    }
    if (item.isPresale) {
        badges.push(`<span class="px-1.5 py-0.5 text-[10px] font-bold bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/50 uppercase">PRESALE</span>`);
    }
    return badges.join(" ");
}

function renderSellerValueResults(data) {
    const kpiBar = document.getElementById("scout-value-kpis");
    const banner = document.getElementById("scout-value-store-banner");
    const warningsBox = document.getElementById("scout-value-warnings");
    const container = document.getElementById("scout-value-results-container");
    if (!container) return;

    const sum = data.summary || {};
    const stats = data.storeStats || {};
    const seller = data.seller || {};

    if (kpiBar) {
        kpiBar.classList.remove("hidden");
        document.getElementById("kpi-scout-watchlist").textContent = String(sum.watchlistCount || 0);
        document.getElementById("kpi-scout-decks").textContent = String(sum.deckUpgradesCount || 0);
        document.getElementById("kpi-scout-value").textContent = String(sum.valuePicksCount || 0);
        document.getElementById("kpi-scout-sealed").textContent = String(sum.sealedPicksCount || 0);
        document.getElementById("kpi-scout-owned-skipped").textContent = `${sum.ownedExcludedCount || 0} owned excluded`;
        document.getElementById("kpi-scout-duration").textContent = `${stats.durationSeconds || 0}s`;
    }

    if (banner) {
        banner.classList.remove("hidden");
        const directTag = seller.directSeller ? `<span class="px-1.5 py-0.5 text-[10px] bg-[#00CED1]/20 text-[#00CED1] border border-[#00CED1]/40 font-bold">TCG DIRECT</span>` : "";
        const goldTag = seller.goldSeller ? `<span class="px-1.5 py-0.5 text-[10px] bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40 font-bold">GOLD STAR</span>` : "";
        const certTag = seller.certifiedShop ? `<span class="px-1.5 py-0.5 text-[10px] bg-[#10B981]/20 text-[#10B981] border border-[#10B981]/40 font-bold">LGS / WPN</span>` : "";
        const ratingStr = seller.sellerRating != null ? `${Number(seller.sellerRating).toFixed(1)}% (${_scoutEsc(seller.sellerSales || "0")} sales)` : "";
        banner.innerHTML = `
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="text-sm font-heading font-black text-white uppercase">${_scoutEsc(seller.sellerName || seller.sellerKey)}</span>
                        <span class="text-[11px] text-[#64748B] font-mono">[${_scoutEsc(seller.sellerKey)}]</span>
                        ${directTag}${goldTag}${certTag}
                    </div>
                    <div class="text-xs text-[#94A3B8] font-mono mt-0.5">
                        ${ratingStr ? ratingStr + " · " : ""}Scanned ${stats.cardsProductsScanned || 0} of ${stats.totalCardsInStore || 0} singles (${stats.totalSealedInStore || 0} sealed in store)
                    </div>
                </div>
                <div class="flex flex-wrap items-center gap-2">
                    <button type="button" onclick="saveCurrentScoutSeller()" class="btn-bridge px-2.5 py-1.5 text-xs text-[#F59E0B] border-[#F59E0B]/50 hover:bg-[#F59E0B] hover:text-[#10141D] font-bold uppercase">
                        ★ Save Seller
                    </button>
                    <button type="button" onclick="runSellerValueScan(true)" class="btn-bridge px-2.5 py-1.5 text-xs text-[#00CED1]">
                        ↻ Refresh Live
                    </button>
                    <a href="${_scoutEsc(seller.storeUrl || '#')}" target="_blank" rel="noopener noreferrer" class="btn-bridge px-2.5 py-1.5 text-xs text-white bg-[#10141D]">
                        Open Store ↗
                    </a>
                </div>
            </div>
        `;
    }

    if (warningsBox) {
        const msgs = [];
        if (stats.partial) {
            msgs.push(`Store inventory was partially scanned (${stats.cardsProductsScanned} of ${stats.totalCardsInStore} items covered within the time/product cap). Highest-market cards were scanned first.`);
        }
        const missingDecks = stats.decksMissingUpgrades || [];
        if (missingDecks.length) {
            const names = missingDecks.slice(0, 4).map((d) => d.name).join(", ");
            const extra = missingDecks.length > 4 ? ` +${missingDecks.length - 4} more` : "";
            msgs.push(`Decks without cached upgrade lists (run Upgrades in Commander Hub to include them): ${names}${extra}.`);
        }
        if (msgs.length) {
            warningsBox.classList.remove("hidden");
            warningsBox.innerHTML = msgs.map((m) => `<div>⚠ ${_scoutEsc(m)}</div>`).join("");
        } else {
            warningsBox.classList.add("hidden");
            warningsBox.innerHTML = "";
        }
    }

    // Build item registry and default selection
    scoutItemRegistry = {};
    selectedScoutKeys = new Set();

    const buckets = [
        { key: "watchlist", title: "Watchlist Hits", accent: "#00CED1", items: data.watchlistHits || [], emptyMsg: "No watchlist singles matched at this seller." },
        { key: "deck_upgrade", title: "Commander Deck Upgrades", accent: "#8B5CF6", items: data.deckUpgrades || [], emptyMsg: "No unowned Commander deck upgrades matched at this seller." },
        { key: "value", title: "Pure Value Picks (Below Market)", accent: "#10B981", items: data.valuePicks || [], emptyMsg: "No cards met your discount & condition filters." },
        { key: "sealed", title: "Sealed Deals & Watched Boxes", accent: "#F59E0B", items: data.sealedPicks || [], emptyMsg: "No below-market or watched sealed products found at this seller." },
    ];

    const hasTrackedDefaults = (data.watchlistHits && data.watchlistHits.length) || (data.deckUpgrades && data.deckUpgrades.length);
    let valueAutoIdx = 0;

    buckets.forEach((b) => {
        b.items.forEach((item, idx) => {
            const itemKey = `${b.key}_${item.productId}_${item.listingId || idx}`;
            item._scoutKey = itemKey;
            scoutItemRegistry[itemKey] = item;
            if (b.key === "watchlist" || b.key === "deck_upgrade") {
                selectedScoutKeys.add(itemKey);
            } else if (b.key === "sealed" && item.isWatchedSealed) {
                selectedScoutKeys.add(itemKey);
            } else if (!hasTrackedDefaults && b.key === "value" && valueAutoIdx < 10) {
                selectedScoutKeys.add(itemKey);
                valueAutoIdx += 1;
            }
        });
    });

    if (!(sum.totalRecommendations > 0)) {
        container.innerHTML = `
            <div class="bg-[#1B2230] border border-[#263245] p-8 text-center font-mono">
                <div class="text-xs text-[#F59E0B] uppercase font-bold">// NO MATCHING DEALS AT CURRENT FILTERS</div>
                <p class="text-xs text-[#94A3B8] font-sans mt-2">
                    Try lowering Min Discount % (e.g. 5%), lowering Min Market Price, or including Lightly/Moderately Played conditions.
                </p>
            </div>
        `;
        recalculateScoutCartBar();
        return;
    }

    container.innerHTML = buckets.map((b) => {
        if (!b.items.length) return "";
        const collapsed = Boolean(scoutBucketCollapsed[b.key]);
        const rowsHtml = b.items.map((item) => {
            const k = _scoutEsc(item._scoutKey);
            const checked = selectedScoutKeys.has(item._scoutKey) ? "checked" : "";
            const subLine = item.isSealed
                ? `${_scoutEsc(item.categoryLabel || "Sealed")}${item.packCount ? ` · ${item.packCount} packs (${_scoutUsd(item.pricePerPack)}/pk)` : ""}`
                : `${_scoutEsc(item.setName || "")}${item.setCode ? ` [${_scoutEsc(item.setCode)}]` : ""} · ${_scoutEsc(item.condition || "NM")} · ${_scoutEsc(item.printing || "Normal")}`;
            const shipStr = item.shippingPrice > 0 ? `+${_scoutUsd(item.shippingPrice)} ship` : "Free/Combined";
            const savStr = item.savingsVsMarket != null
                ? (item.savingsVsMarket >= 0 ? `Save ${_scoutUsd(item.savingsVsMarket)}` : `+${_scoutUsd(Math.abs(item.savingsVsMarket))} vs mkt`)
                : "";
            return `
                <div class="bg-[#10141D] border border-[#263245] hover:border-[#3D4F6B] p-2.5 flex items-start gap-2.5 transition">
                    <input type="checkbox" ${checked} onchange="toggleScoutItemSelection('${k}', this.checked)"
                        class="mt-1 form-checkbox text-[#10B981] rounded-none bg-[#161C28] border-[#263245] cursor-pointer flex-shrink-0">
                    <img src="${_scoutEsc(item.imageUrl || '')}" alt="" loading="lazy"
                        class="w-11 h-14 object-contain bg-[#161C28] border border-[#263245] flex-shrink-0"
                        onerror="this.style.display='none'">
                    <div class="min-w-0 flex-1 space-y-1">
                        <div class="flex items-start justify-between gap-2">
                            <a href="${_scoutEsc(item.tcgplayerUrl || '#')}" target="_blank" rel="noopener noreferrer"
                                class="text-xs font-bold text-white hover:text-[#10B981] truncate">
                                ${_scoutEsc(item.productName)}
                            </a>
                            <div class="text-right flex-shrink-0 font-mono">
                                <div class="text-xs font-bold text-[#10B981]">${_scoutUsd(item.price)}</div>
                                <div class="text-[10px] text-[#64748B]">Mkt ${_scoutUsd(item.marketPrice)}</div>
                            </div>
                        </div>
                        <div class="text-[11px] text-[#94A3B8] truncate font-mono">${subLine}</div>
                        <div class="flex flex-wrap items-center justify-between gap-1.5">
                            <div class="flex flex-wrap items-center gap-1">${_buildReasonBadgesHtml(item)}</div>
                            <div class="text-[10px] font-mono text-[#94A3B8]">
                                ${savStr ? `<span class="text-white font-bold">${_scoutEsc(savStr)}</span> · ` : ""}Qty ${Number(item.quantity || 1)} · ${shipStr}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join("");

        return `
            <div class="bg-[#1B2230] border border-[#263245]">
                <button type="button" onclick="toggleScoutBucket('${b.key}')"
                    class="w-full px-4 py-2.5 flex items-center justify-between border-b border-[#263245] hover:bg-[#161C28] transition font-mono">
                    <div class="flex items-center space-x-2">
                        <span class="w-2 h-2 inline-block" style="background-color: ${b.accent};"></span>
                        <span class="text-xs font-bold uppercase tracking-wider text-white">${_scoutEsc(b.title)}</span>
                        <span class="text-[11px] px-1.5 py-0.2 bg-[#10141D] border border-[#263245] text-[#94A3B8]">${b.items.length}</span>
                    </div>
                    <span id="scout-bucket-chevron-${b.key}" class="text-xs text-[#94A3B8]">${collapsed ? "▼ Show" : "▲ Collapse"}</span>
                </button>
                <div id="scout-bucket-body-${b.key}" class="${collapsed ? "hidden" : ""} p-3 grid grid-cols-1 md:grid-cols-2 gap-2.5">
                    ${rowsHtml}
                </div>
            </div>
        `;
    }).join("");

    recalculateScoutCartBar();
}

function toggleScoutBucket(bucketKey) {
    scoutBucketCollapsed[bucketKey] = !scoutBucketCollapsed[bucketKey];
    const body = document.getElementById(`scout-bucket-body-${bucketKey}`);
    const chev = document.getElementById(`scout-bucket-chevron-${bucketKey}`);
    if (body) body.classList.toggle("hidden", scoutBucketCollapsed[bucketKey]);
    if (chev) chev.textContent = scoutBucketCollapsed[bucketKey] ? "▼ Show" : "▲ Collapse";
}

function toggleScoutItemSelection(itemKey, isChecked) {
    if (isChecked) selectedScoutKeys.add(itemKey);
    else selectedScoutKeys.delete(itemKey);
    recalculateScoutCartBar();
}

function selectScoutPreset(preset) {
    if (!currentSellerScanResult) return;
    selectedScoutKeys.clear();
    if (preset === "recommended") {
        (currentSellerScanResult.watchlistHits || []).forEach((it) => selectedScoutKeys.add(it._scoutKey));
        (currentSellerScanResult.deckUpgrades || []).forEach((it) => selectedScoutKeys.add(it._scoutKey));
        (currentSellerScanResult.sealedPicks || []).filter((it) => it.isWatchedSealed).forEach((it) => selectedScoutKeys.add(it._scoutKey));
        if (!selectedScoutKeys.size) {
            (currentSellerScanResult.valuePicks || []).slice(0, 10).forEach((it) => selectedScoutKeys.add(it._scoutKey));
        }
    } else if (preset === "top10_value") {
        (currentSellerScanResult.valuePicks || []).slice(0, 10).forEach((it) => selectedScoutKeys.add(it._scoutKey));
    } else if (preset === "all") {
        Object.keys(scoutItemRegistry).forEach((k) => selectedScoutKeys.add(k));
    }
    renderSyncCheckboxes();
    recalculateScoutCartBar();
}

function renderSyncCheckboxes() {
    const container = document.getElementById("scout-value-results-container");
    if (!container) return;
    container.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
        const attr = cb.getAttribute("onchange") || "";
        const m = attr.match(/toggleScoutItemSelection\('([^']+)'/);
        if (m) {
            cb.checked = selectedScoutKeys.has(m[1]);
        }
    });
}

function recalculateScoutCartBar() {
    const bar = document.getElementById("scout-sticky-cart-bar");
    if (!bar) return;
    const selected = Array.from(selectedScoutKeys).map((k) => scoutItemRegistry[k]).filter(Boolean);
    if (!selected.length) {
        bar.classList.add("hidden");
        return;
    }

    const singles = selected.filter((x) => !x.isSealed);
    const sealed = selected.filter((x) => x.isSealed);
    const subtotal = selected.reduce((acc, x) => acc + Number(x.price || 0), 0);
    const marketSum = selected.reduce((acc, x) => acc + Number(x.marketPrice || x.price || 0), 0);
    const sealedShip = sealed.reduce((m, x) => Math.max(m, Number(x.shippingPrice || 0)), 0);
    const maxSingleShip = singles.reduce((m, x) => Math.max(m, Number(x.shippingPrice || 0)), 0);
    const isDirect = Boolean(currentSellerScanResult && currentSellerScanResult.seller && currentSellerScanResult.seller.directSeller);

    let effSingleShip = maxSingleShip;
    if (singles.length && subtotal >= 5.0 && maxSingleShip <= 1.99) effSingleShip = 0;
    else if (singles.length && isDirect && subtotal >= 50.0 && !sealed.length) effSingleShip = 0;

    const estShip = Math.max(sealedShip, effSingleShip);
    const landed = subtotal + estShip;
    const netSav = marketSum - landed;

    bar.classList.remove("hidden");
    document.getElementById("scout-cart-count").textContent = `${selected.length} items (${singles.length} singles, ${sealed.length} sealed)`;
    document.getElementById("scout-cart-subtotal").textContent = _scoutUsd(subtotal);
    document.getElementById("scout-cart-shipping").textContent = estShip > 0 ? _scoutUsd(estShip) : "FREE";
    document.getElementById("scout-cart-savings").textContent = _scoutUsd(Math.max(0, netSav));

    const storeLink = document.getElementById("scout-cart-store-link");
    if (storeLink && currentSellerScanResult && currentSellerScanResult.seller) {
        storeLink.href = currentSellerScanResult.seller.storeUrl || "#";
    }
}

function _buildSelectedMassEntryText() {
    const selected = Array.from(selectedScoutKeys).map((k) => scoutItemRegistry[k]).filter(Boolean);
    const singles = selected.filter((x) => !x.isSealed);
    const seen = new Set();
    const lines = [];
    singles.forEach((c) => {
        const cname = (c.cleanName || c.productName || "").split(" // ")[0].trim();
        if (!cname) return;
        const low = cname.toLowerCase();
        if (seen.has(low)) return;
        seen.add(low);
        lines.push(`1 ${cname}`);
    });
    return { text: lines.join("\n"), singlesCount: lines.length, sealedCount: selected.length - singles.length };
}

function copyScoutMassEntry() {
    const info = _buildSelectedMassEntryText();
    if (!info.text) {
        _scoutToast("Select at least one single card first (sealed items open directly via their link).", "warning");
        return;
    }
    navigator.clipboard.writeText(info.text).then(() => {
        const extra = info.sealedCount > 0 ? ` (open ${info.sealedCount} sealed item(s) directly via link)` : "";
        _scoutToast(`Copied ${info.singlesCount} singles for TCGplayer Mass Entry${extra}!`, "success");
    }).catch(() => {
        _scoutToast("Failed to copy Mass Entry list.", "error");
    });
}

function openScoutMassEntry() {
    const info = _buildSelectedMassEntryText();
    if (info.text) {
        navigator.clipboard.writeText(info.text).then(() => {
            _scoutToast(`Copied ${info.singlesCount} cards — opening TCGplayer Mass Entry.`, "success");
        }).catch(() => {});
    }
    window.open("https://www.tcgplayer.com/massentry", "_blank", "noopener,noreferrer");
}

// =========================================================================
// Combined Sealed + Singles Cart Finder
// =========================================================================
async function runCombinedCartsScan() {
    const btn = document.getElementById("btn-run-combined-carts");
    const btnLabel = document.getElementById("combined-scan-btn-label");
    const spinner = document.getElementById("combined-scan-spinner");
    const statusText = document.getElementById("combined-status-text");

    if (btn) btn.disabled = true;
    if (spinner) spinner.classList.remove("hidden");
    if (btnLabel) btnLabel.textContent = "SCANNING COMBINED SELLERS...";
    if (statusText) statusText.textContent = "Checking sellers with both wanted sealed items and your watchlist singles...";

    const payload = {
        min_sealed: parseInt(document.getElementById("combined-min-sealed")?.value || "1", 10),
        min_singles: parseInt(document.getElementById("combined-min-singles")?.value || "1", 10),
        price_tolerance_mode: document.getElementById("combined-price-tolerance")?.value || "decent_10",
        sealed_min_discount_pct: parseFloat(document.getElementById("combined-sealed-discount")?.value || "0"),
        tag_filter: document.getElementById("combined-tag-filter")?.value || "all",
        english_only: Boolean(document.getElementById("combined-english-only")?.checked),
        allow_mp: Boolean(document.getElementById("combined-allow-mp")?.checked),
    };

    try {
        const resp = await fetch("/api/tcgplayer/combined-carts", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            _scoutToast(data.error || "Combined cart scan failed.", "error");
            if (statusText) statusText.textContent = data.error || "Combined cart scan failed.";
            return;
        }
        currentCombinedCartsResult = data;
        renderCombinedCartsResults(data);
        if (statusText) {
            statusText.textContent = `Scanned ${data.candidateSellersScanned || 0} candidate sellers in ${data.durationSeconds || 0}s — found ${data.totalCartsFound || 0} combined carts.`;
        }
        _scoutToast(`Found ${data.totalCartsFound || 0} combined sealed + singles carts.`, "success");
    } catch (err) {
        console.error("Combined cart scan error:", err);
        _scoutToast("Network error running combined cart scan.", "error");
    } finally {
        if (btn) btn.disabled = false;
        if (spinner) spinner.classList.add("hidden");
        if (btnLabel) btnLabel.textContent = "🛒 FIND COMBINED CARTS";
    }
}

function renderCombinedCartsResults(data) {
    const kpiBar = document.getElementById("combined-kpis");
    const container = document.getElementById("combined-results-container");
    if (!container) return;

    const carts = data.carts || [];
    if (kpiBar) {
        kpiBar.classList.remove("hidden");
        document.getElementById("kpi-combined-carts-count").textContent = String(carts.length);
        const bestNet = carts.reduce((m, c) => Math.max(m, Number(c.netSavings || 0)), 0);
        const bestShip = carts.reduce((m, c) => Math.max(m, Number(c.shippingSaved || 0)), 0);
        document.getElementById("kpi-combined-best-savings").textContent = _scoutUsd(bestNet);
        document.getElementById("kpi-combined-ship-saved").textContent = _scoutUsd(bestShip);
        document.getElementById("kpi-combined-duration").textContent = `${data.durationSeconds || 0}s`;
    }

    if (!carts.length) {
        container.innerHTML = `
            <div class="bg-[#1B2230] border border-[#263245] p-8 text-center font-mono">
                <div class="text-xs text-[#F59E0B] uppercase font-bold">// NO COMBINED CARTS MATCHED</div>
                <p class="text-xs text-[#94A3B8] font-sans mt-2 max-w-lg mx-auto">
                    ${_scoutEsc(data.message || "No single seller currently matched both your sealed threshold and watchlist singles at the chosen price tolerance. Try widening Singles Price Rule or adding sealed products to your watchlist.")}
                </p>
            </div>
        `;
        return;
    }

    container.innerHTML = carts.map((cart, idx) => {
        const directTag = cart.directSeller ? `<span class="px-1.5 py-0.5 text-[10px] bg-[#00CED1]/20 text-[#00CED1] border border-[#00CED1]/40 font-bold">DIRECT</span>` : "";
        const goldTag = cart.goldSeller ? `<span class="px-1.5 py-0.5 text-[10px] bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40 font-bold">GOLD</span>` : "";
        const sealedRows = (cart.sealedItems || []).map((s) => `
            <tr class="border-t border-[#263245]/60">
                <td class="py-1.5 pr-2">
                    <a href="${_scoutEsc(s.tcgplayerUrl)}" target="_blank" rel="noopener noreferrer" class="text-white hover:text-[#F59E0B] font-bold">
                        ${_scoutEsc(s.productName)}
                    </a>
                    ${s.isWatchedSealed ? `<span class="ml-1 px-1 py-0.2 text-[9px] bg-[#F59E0B]/20 text-[#F59E0B] border border-[#F59E0B]/40">WATCHED</span>` : ""}
                </td>
                <td class="py-1.5 px-2 text-right font-mono text-[#10B981] font-bold">${_scoutUsd(s.price)}</td>
                <td class="py-1.5 px-2 text-right font-mono text-[#94A3B8]">${_scoutUsd(s.marketPrice)}</td>
                <td class="py-1.5 pl-2 text-right font-mono text-[#94A3B8]">${s.discountPct != null ? `-${Number(s.discountPct).toFixed(1)}%` : "-"}</td>
            </tr>
        `).join("");

        const singleRows = (cart.singleItems || []).map((c) => `
            <tr class="border-t border-[#263245]/60">
                <td class="py-1.5 pr-2">
                    <a href="${_scoutEsc(c.tcgplayerUrl)}" target="_blank" rel="noopener noreferrer" class="text-white hover:text-[#00CED1] font-bold">
                        ${_scoutEsc(c.name)}
                    </a>
                    <span class="text-[10px] text-[#64748B] ml-1">${_scoutEsc(c.setName || "")} · ${_scoutEsc(c.condition || "NM")}</span>
                </td>
                <td class="py-1.5 px-2 text-right font-mono text-[#10B981] font-bold">${_scoutUsd(c.price)}</td>
                <td class="py-1.5 px-2 text-right font-mono text-[#94A3B8]">${_scoutUsd(c.marketPrice)}</td>
                <td class="py-1.5 pl-2 text-right font-mono text-[#94A3B8]">${c.deltaPct != null ? `${Number(c.deltaPct) > 0 ? "+" : ""}${Number(c.deltaPct).toFixed(1)}%` : _scoutEsc(c.priceLabel || "")}</td>
            </tr>
        `).join("");

        return `
            <div class="bg-[#1B2230] border border-[#263245] p-4 space-y-3">
                <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-[#263245] pb-3">
                    <div>
                        <div class="flex flex-wrap items-center gap-2">
                            <span class="text-sm font-heading font-black text-white uppercase">${_scoutEsc(cart.sellerName)}</span>
                            <span class="text-[11px] font-mono text-[#64748B]">[${_scoutEsc(cart.sellerKey)}]</span>
                            ${directTag}${goldTag}
                        </div>
                        <div class="text-xs font-mono text-[#94A3B8] mt-0.5">
                            ${cart.sealedCount} Sealed + ${cart.singlesCount} Singles · Landed <strong class="text-white">${_scoutUsd(cart.landedTotal)}</strong> (Ship ${cart.estShipping > 0 ? _scoutUsd(cart.estShipping) : "FREE"}, Saved ${_scoutUsd(cart.shippingSaved)} ship)
                        </div>
                    </div>
                    <div class="flex flex-wrap items-center gap-2">
                        <span class="px-2 py-1 text-xs font-mono font-bold bg-[#10B981]/15 text-[#10B981] border border-[#10B981]/40">
                            Net Save ${_scoutUsd(cart.netSavings)}
                        </span>
                        <button type="button" onclick="saveSellerFromCombinedCart(${idx})" class="btn-bridge px-2 py-1 text-xs text-[#F59E0B]">★ Save</button>
                        <button type="button" onclick="copyCombinedCartMassEntry(${idx})" class="btn-bridge px-2 py-1 text-xs text-[#00CED1]">Copy Mass Entry</button>
                        <a href="${_scoutEsc(cart.multiCardSearchUrl)}" target="_blank" rel="noopener noreferrer" class="btn-bridge px-2 py-1 text-xs text-white bg-[#10141D]">Search Singles ↗</a>
                        <a href="${_scoutEsc(cart.storeUrl)}" target="_blank" rel="noopener noreferrer" class="btn-bridge px-2 py-1 text-xs text-white bg-[#10141D]">Store ↗</a>
                    </div>
                </div>

                <div class="grid grid-cols-1 lg:grid-cols-2 gap-3 text-xs">
                    <div class="bg-[#10141D] border border-[#263245] p-2.5 overflow-x-auto">
                        <div class="text-[10px] font-mono font-bold text-[#F59E0B] uppercase mb-1.5">📦 Sealed Products (${cart.sealedCount} · ${_scoutUsd(cart.sealedSubtotal)})</div>
                        <table class="w-full text-left border-collapse">
                            <thead><tr class="text-[10px] font-mono text-[#64748B]"><th>Product</th><th class="text-right px-2">Price</th><th class="text-right px-2">Market</th><th class="text-right">vs Mkt</th></tr></thead>
                            <tbody>${sealedRows}</tbody>
                        </table>
                    </div>
                    <div class="bg-[#10141D] border border-[#263245] p-2.5 overflow-x-auto">
                        <div class="text-[10px] font-mono font-bold text-[#00CED1] uppercase mb-1.5">🃏 Watchlist Singles (${cart.singlesCount} · ${_scoutUsd(cart.singlesSubtotal)})</div>
                        <table class="w-full text-left border-collapse">
                            <thead><tr class="text-[10px] font-mono text-[#64748B]"><th>Card</th><th class="text-right px-2">Price</th><th class="text-right px-2">Market</th><th class="text-right">Delta</th></tr></thead>
                            <tbody>${singleRows}</tbody>
                        </table>
                    </div>
                </div>
            </div>
        `;
    }).join("");
}

function copyCombinedCartMassEntry(cartIndex) {
    const carts = (currentCombinedCartsResult && currentCombinedCartsResult.carts) || [];
    const cart = carts[cartIndex];
    if (!cart || !cart.massEntryText) {
        _scoutToast("No singles in this cart to copy.", "warning");
        return;
    }
    navigator.clipboard.writeText(cart.massEntryText).then(() => {
        _scoutToast(`Copied ${cart.singlesCount} singles for ${cart.sellerName} Mass Entry!`, "success");
    }).catch(() => {
        _scoutToast("Failed to copy Mass Entry list.", "error");
    });
}

async function saveSellerFromCombinedCart(cartIndex) {
    const carts = (currentCombinedCartsResult && currentCombinedCartsResult.carts) || [];
    const cart = carts[cartIndex];
    if (!cart) return;
    try {
        const resp = await fetch("/api/tcgplayer/saved-sellers", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                seller_key: cart.sellerKey,
                seller_name: cart.sellerName,
                note: "Combined Sealed + Singles",
            }),
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) {
            _scoutToast(data.error || "Could not save seller.", "error");
            return;
        }
        if (Array.isArray(data.sellers)) {
            savedTcgSellers = data.sellers;
            renderSavedSellerChips();
        }
        _scoutToast(`Saved ${cart.sellerName}!`, "success");
    } catch (err) {
        _scoutToast("Network error saving seller.", "error");
    }
}
