// =========================================================================
// BUYLIST SCANNER // CLIENT CONTROLS & BATCH VALUATION ENGINE
// =========================================================================

let activeBuylistMode = "single";
let buylistSearchResults = [];
let bulkBuylistQuotes = [];
let bulkBuylistSummary = null;
let currentModalVariants = [];
let buylistCart = [];

function initBuylistCart() {
    try {
        const saved = localStorage.getItem("chimaera_buylist_cart");
        if (saved) {
            buylistCart = JSON.parse(saved);
        }
    } catch (e) {
        console.error("Failed to load buylist cart:", e);
        buylistCart = [];
    }
    renderBuylistCart();
}

function saveBuylistCart() {
    try {
        localStorage.setItem("chimaera_buylist_cart", JSON.stringify(buylistCart));
    } catch (e) {
        console.error("Failed to save buylist cart:", e);
    }
    renderBuylistCart();
}

function toggleBuylistCartDrawer() {
    const modal = document.getElementById("modal-buylist-cart");
    if (!modal) return;
    modal.classList.toggle("hidden");
    if (!modal.classList.contains("hidden")) {
        renderBuylistCart();
    }
}

function addToBuylistCart(item) {
    if (!item || !item.card_name) return;

    const cardName = item.card_name.trim();
    const setName = item.set_name ? item.set_name.trim() : "Unknown Set";
    const condition = item.condition || "Lightly Played";
    const finish = item.finish || "nonfoil";
    const creditPrice = parseFloat(item.credit_price || 0);
    const cashPrice = parseFloat(item.cash_price || 0);
    const sellPrice = parseFloat(item.store_sell_price || 0);
    const imageUrl = item.image_url || "";
    const addQty = parseInt(item.qty || 1, 10);

    const existingIndex = buylistCart.findIndex(c => 
        c.card_name.toLowerCase() === cardName.toLowerCase() &&
        c.set_name.toLowerCase() === setName.toLowerCase() &&
        c.condition.toLowerCase() === condition.toLowerCase() &&
        c.finish.toLowerCase() === finish.toLowerCase()
    );

    if (existingIndex > -1) {
        buylistCart[existingIndex].qty += addQty;
    } else {
        buylistCart.push({
            card_name: cardName,
            set_name: setName,
            condition: condition,
            finish: finish,
            credit_price: creditPrice,
            cash_price: cashPrice,
            store_sell_price: sellPrice,
            image_url: imageUrl,
            qty: addQty
        });
    }

    saveBuylistCart();
    showToast(`Added ${addQty}x ${cardName} (${condition}) to Trade-In Batch ($${(creditPrice * addQty).toFixed(2)} Store Credit)`, "success");

    // Re-render search results to show active batch counts if present
    if (activeBuylistMode === "single" && buylistSearchResults.length > 0) {
        renderSingleBuylistResults();
    } else if (activeBuylistMode === "bulk" && bulkBuylistQuotes.length > 0) {
        renderBulkBuylistResults();
    }
}

function getCardCartQty(cardName, setName = null, condition = null) {
    if (!buylistCart || buylistCart.length === 0) return 0;
    const nameLower = cardName.toLowerCase().trim();
    let count = 0;
    for (const c of buylistCart) {
        if (c.card_name.toLowerCase() === nameLower) {
            if (!setName || c.set_name.toLowerCase() === setName.toLowerCase()) {
                if (!condition || c.condition.toLowerCase() === condition.toLowerCase()) {
                    count += c.qty;
                }
            }
        }
    }
    return count;
}

function addSingleResultToCart(idx) {
    const card = buylistSearchResults[idx];
    if (!card) return;

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    const vMatch = getMatchingVariant(card.variants, selectedCond, "nonfoil");

    addToBuylistCart({
        card_name: card.card_name,
        set_name: card.set_name,
        condition: vMatch ? vMatch.condition : selectedCond,
        finish: vMatch ? vMatch.finish : "Normal",
        credit_price: vMatch ? vMatch.credit_price : 0,
        cash_price: vMatch ? vMatch.cash_price : 0,
        store_sell_price: vMatch ? vMatch.store_sell_price : 0,
        image_url: card.image_url || "",
        qty: 1
    });
}

function addVariantToCart(cardName, setName, condition, finish, creditPrice, cashPrice, sellPrice, imgUrl) {
    addToBuylistCart({
        card_name: cardName,
        set_name: setName,
        condition: condition,
        finish: finish,
        credit_price: creditPrice,
        cash_price: cashPrice,
        store_sell_price: sellPrice,
        image_url: imgUrl,
        qty: 1
    });
}

function addBulkQuoteToCart(idx) {
    const q = bulkBuylistQuotes[idx];
    if (!q || !q.matched) return;

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    let cPrice = q.credit_price;
    let kPrice = q.cash_price;
    let sPrice = q.store_sell_price;
    let cond = q.condition || selectedCond;
    let finish = q.finish || "Normal";

    if (q.all_prints && q.all_prints.length > 0) {
        const v = getMatchingVariant(q.all_prints[0].variants, selectedCond, "nonfoil");
        if (v) {
            cPrice = v.credit_price;
            kPrice = v.cash_price;
            sPrice = v.store_sell_price;
            cond = v.condition;
            finish = v.finish;
        }
    }

    addToBuylistCart({
        card_name: q.card_name,
        set_name: q.set_name,
        condition: cond,
        finish: finish,
        credit_price: cPrice,
        cash_price: kPrice,
        store_sell_price: sPrice,
        image_url: q.image_url || "",
        qty: 1
    });
}

function addAllBulkQuotesToCart() {
    if (!bulkBuylistQuotes || bulkBuylistQuotes.length === 0) {
        showToast("No bulk quotes available to add", "info");
        return;
    }

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    let addedCount = 0;

    bulkBuylistQuotes.forEach(q => {
        if (q.matched) {
            let cPrice = q.credit_price;
            let kPrice = q.cash_price;
            let sPrice = q.store_sell_price;
            let cond = q.condition || selectedCond;
            let finish = q.finish || "Normal";

            if (q.all_prints && q.all_prints.length > 0) {
                const v = getMatchingVariant(q.all_prints[0].variants, selectedCond, "nonfoil");
                if (v) {
                    cPrice = v.credit_price;
                    kPrice = v.cash_price;
                    sPrice = v.store_sell_price;
                    cond = v.condition;
                    finish = v.finish;
                }
            }

            const existingIndex = buylistCart.findIndex(c => 
                c.card_name.toLowerCase() === q.card_name.toLowerCase() &&
                c.set_name.toLowerCase() === (q.set_name || '').toLowerCase() &&
                c.condition.toLowerCase() === cond.toLowerCase() &&
                c.finish.toLowerCase() === finish.toLowerCase()
            );

            if (existingIndex > -1) {
                buylistCart[existingIndex].qty += 1;
            } else {
                buylistCart.push({
                    card_name: q.card_name,
                    set_name: q.set_name || "Unknown Set",
                    condition: cond,
                    finish: finish,
                    credit_price: cPrice,
                    cash_price: kPrice,
                    store_sell_price: sPrice,
                    image_url: q.image_url || "",
                    qty: 1
                });
            }
            addedCount++;
        }
    });

    saveBuylistCart();
    showToast(`Added ${addedCount} cards to Trade-In Batch!`, "success");
    renderBulkBuylistResults();
}

function updateBuylistCartQty(idx, delta) {
    if (!buylistCart[idx]) return;
    buylistCart[idx].qty += delta;
    if (buylistCart[idx].qty <= 0) {
        buylistCart.splice(idx, 1);
    }
    saveBuylistCart();
}

function removeFromBuylistCart(idx) {
    if (!buylistCart[idx]) return;
    const removed = buylistCart.splice(idx, 1)[0];
    saveBuylistCart();
    showToast(`Removed ${removed.card_name} from Trade-In Batch`, "info");
}

function clearBuylistCart() {
    if (buylistCart.length === 0) return;
    buylistCart = [];
    saveBuylistCart();
    showToast("Trade-In Batch cleared", "info");
}

function renderBuylistCart() {
    const badge = document.getElementById("buylist-cart-badge");
    const container = document.getElementById("buylist-cart-items-container");
    const totalQtyEl = document.getElementById("cart-total-qty");
    const totalCreditEl = document.getElementById("cart-total-credit");
    const totalCashEl = document.getElementById("cart-total-cash");

    let totalQty = 0;
    let totalCredit = 0.0;
    let totalCash = 0.0;

    buylistCart.forEach(item => {
        totalQty += item.qty;
        totalCredit += (item.credit_price * item.qty);
        totalCash += (item.cash_price * item.qty);
    });

    if (badge) badge.textContent = totalQty;
    if (totalQtyEl) totalQtyEl.textContent = totalQty;
    if (totalCreditEl) totalCreditEl.textContent = `$${totalCredit.toFixed(2)}`;
    if (totalCashEl) totalCashEl.textContent = `$${totalCash.toFixed(2)}`;

    if (!container) return;

    if (buylistCart.length === 0) {
        container.innerHTML = `
            <div class="bg-[#10141D] border border-[#263245] p-8 text-center text-[#94A3B8] space-y-2">
                <div class="w-10 h-10 bg-[#1B2230] border border-[#263245] flex items-center justify-center mx-auto text-[#2DD4BF] text-lg font-bold">📦</div>
                <div class="text-xs font-bold text-white uppercase tracking-wider">Trade-In Batch Empty</div>
                <p class="text-[11px] text-[#64748B] max-w-xs mx-auto">
                    Click <strong>+ Trade-In</strong> on any single card quote or bulk manifest search to build your trade-in order.
                </p>
            </div>
        `;
        return;
    }

    let html = "";
    buylistCart.forEach((item, idx) => {
        const itemCreditSubtotal = (item.credit_price * item.qty).toFixed(2);
        const itemCashSubtotal = (item.cash_price * item.qty).toFixed(2);

        html += `
            <div class="bg-[#10141D] border border-[#263245] hover:border-[#2DD4BF]/50 p-2.5 flex items-center justify-between gap-3 transition">
                <div class="flex items-center space-x-2.5 min-w-0 flex-1">
                    ${item.image_url ? `
                    <img src="${toScryfallThumb(item.image_url)}" alt="" loading="lazy" decoding="async" class="w-9 h-12 object-cover border border-[#263245] bg-[#0C0F17] flex-shrink-0" onerror="this.style.display='none'">
                    ` : ''}
                    <div class="min-w-0 flex-1">
                        <div class="font-bold text-white text-xs truncate" title="${item.card_name}">
                            ${item.card_name}
                        </div>
                        <div class="text-[10px] text-[#94A3B8] truncate">
                            ${item.set_name} &bull; <span class="text-[#2DD4BF] font-bold">${item.condition}</span> (${item.finish})
                        </div>
                        <div class="text-[10px] text-[#64748B] mt-0.5">
                            Unit: <span class="text-[#2DD4BF] font-bold">$${item.credit_price.toFixed(2)}</span> Credit &bull; $${item.cash_price.toFixed(2)} Cash
                        </div>
                    </div>
                </div>

                <!-- Quantity and Subtotals -->
                <div class="flex items-center space-x-3 flex-shrink-0">
                    <!-- Qty Controls -->
                    <div class="flex items-center border border-[#263245] bg-[#1B2230]">
                        <button onclick="updateBuylistCartQty(${idx}, -1)" class="min-w-[36px] min-h-[36px] inline-flex items-center justify-center text-xs text-[#94A3B8] hover:text-white hover:bg-[#263245]">-</button>
                        <span class="px-2 text-xs font-bold text-white">${item.qty}</span>
                        <button onclick="updateBuylistCartQty(${idx}, 1)" class="min-w-[36px] min-h-[36px] inline-flex items-center justify-center text-xs text-[#94A3B8] hover:text-white hover:bg-[#263245]">+</button>
                    </div>

                    <!-- Price Subtotal -->
                    <div class="text-right min-w-[70px]">
                        <div class="font-bold text-xs text-[#2DD4BF]">$${itemCreditSubtotal}</div>
                        <div class="text-[10px] text-[#94A3B8]">$${itemCashSubtotal} Cash</div>
                    </div>

                    <!-- Remove Item -->
                    <button onclick="removeFromBuylistCart(${idx})" class="text-[#64748B] hover:text-[#FF3358] text-base font-bold min-w-[36px] min-h-[36px] inline-flex items-center justify-center" title="Remove item">&times;</button>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

function exportBuylistCartCSV() {
    if (!buylistCart || buylistCart.length === 0) {
        showToast("Trade-In Batch is currently empty", "info");
        return;
    }

    let csvContent = "\uFEFF"; // UTF-8 BOM
    csvContent += "Card Name,Set,Condition Grade,Finish,Quantity,Unit Store Credit ($),Total Store Credit ($),Unit Cash Buy ($),Total Cash Buy ($)\n";

    buylistCart.forEach(item => {
        const cleanName = `"${(item.card_name || '').replace(/"/g, '""')}"`;
        const cleanSet = `"${(item.set_name || '').replace(/"/g, '""')}"`;
        const totalCredit = (item.credit_price * item.qty).toFixed(2);
        const totalCash = (item.cash_price * item.qty).toFixed(2);
        csvContent += `${cleanName},${cleanSet},${item.condition},${item.finish},${item.qty},${item.credit_price.toFixed(2)},${totalCredit},${item.cash_price.toFixed(2)},${totalCash}\n`;
    });

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `mightymeeple_trade_in_batch_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showToast("Downloaded Trade-In Batch CSV", "success");
}

function copyBuylistCartSummary() {
    if (!buylistCart || buylistCart.length === 0) {
        showToast("Trade-In Batch is currently empty", "info");
        return;
    }

    let summaryText = `MIGHTY MEEPLE TRADE-IN BATCH MANIFEST\n`;
    summaryText += `Generated: ${formatESTDate(new Date())}\n`;
    summaryText += `--------------------------------------------------\n`;

    let totalQty = 0;
    let totalCredit = 0.0;
    let totalCash = 0.0;

    buylistCart.forEach(item => {
        const itemCredit = item.credit_price * item.qty;
        const itemCash = item.cash_price * item.qty;
        totalQty += item.qty;
        totalCredit += itemCredit;
        totalCash += itemCash;
        summaryText += `${item.qty}x ${item.card_name} [${item.set_name}] (${item.condition} - ${item.finish}) -> Credit: $${itemCredit.toFixed(2)} | Cash: $${itemCash.toFixed(2)}\n`;
    });

    summaryText += `--------------------------------------------------\n`;
    summaryText += `TOTAL CARDS:        ${totalQty}\n`;
    summaryText += `TOTAL STORE CREDIT: $${totalCredit.toFixed(2)}\n`;
    summaryText += `TOTAL CASH PAYOUT:  $${totalCash.toFixed(2)}\n`;

    navigator.clipboard.writeText(summaryText).then(() => {
        showToast("Copied Trade-In Batch Summary to clipboard", "success");
    }).catch(() => {
        showToast("Failed to copy to clipboard", "error");
    });
}

function setBuylistMode(mode) {
    activeBuylistMode = mode;
    const btnSingle = document.getElementById("btn-mode-single");
    const btnBulk = document.getElementById("btn-mode-bulk");
    const singleContainer = document.getElementById("buylist-single-container");
    const bulkContainer = document.getElementById("buylist-bulk-container");

    if (mode === "single") {
        if (btnSingle) btnSingle.classList.add("active");
        if (btnBulk) btnBulk.classList.remove("active");
        if (singleContainer) singleContainer.classList.remove("hidden");
        if (bulkContainer) bulkContainer.classList.add("hidden");
    } else {
        if (btnSingle) btnSingle.classList.remove("active");
        if (btnBulk) btnBulk.classList.add("active");
        if (singleContainer) singleContainer.classList.add("hidden");
        if (bulkContainer) bulkContainer.classList.remove("hidden");
    }
}

function onBuylistPreferenceChange() {
    if (activeBuylistMode === "single" && buylistSearchResults.length > 0) {
        renderSingleBuylistResults();
    } else if (activeBuylistMode === "bulk" && bulkBuylistQuotes.length > 0) {
        renderBulkBuylistResults();
    }
}

function onBuylistGameChange() {
    if (activeBuylistMode === "single") {
        const query = (document.getElementById("buylist-search-input")?.value || "").trim();
        if (query) {
            triggerSingleBuylistSearch();
        }
    }
}

// -------------------------------------------------------------------------
// Single Card Search
// -------------------------------------------------------------------------
async function triggerSingleBuylistSearch() {
    const input = document.getElementById("buylist-search-input");
    const query = (input?.value || "").trim();
    if (!query) {
        showToast("Please enter a card name to search buylist", "info");
        return;
    }

    const game = document.getElementById("buylist-global-game")?.value || "mtg";
    const loadingEl = document.getElementById("single-search-loading");
    const resultsContainer = document.getElementById("single-search-results");
    const clearBtn = document.getElementById("btn-clear-single-search");

    if (loadingEl) loadingEl.classList.remove("hidden");
    if (resultsContainer) resultsContainer.innerHTML = "";
    if (clearBtn) clearBtn.classList.remove("hidden");

    try {
        const res = await fetch(`/api/buylist/search?q=${encodeURIComponent(query)}&game=${encodeURIComponent(game)}&limit=25`);
        const data = await res.json();

        if (res.ok && data.items) {
            buylistSearchResults = data.items;
            renderSingleBuylistResults();
            if (data.items.length === 0) {
                showToast(`No buylist entries found for '${query}'`, "info");
            } else {
                showToast(`Found ${data.items.length} buylist printings`, "success");
            }
        } else {
            showToast(data.error || "Failed to search buylist", "error");
        }
    } catch (err) {
        console.error("Buylist search error:", err);
        showToast("Network error querying buylist", "error");
    } finally {
        if (loadingEl) loadingEl.classList.add("hidden");
    }
}

function quickBuylistSearch(cardName) {
    const input = document.getElementById("buylist-search-input");
    if (input) {
        input.value = cardName;
        triggerSingleBuylistSearch();
    }
}

function clearSingleBuylistSearch() {
    const input = document.getElementById("buylist-search-input");
    const clearBtn = document.getElementById("btn-clear-single-search");
    const resultsContainer = document.getElementById("single-search-results");

    if (input) input.value = "";
    if (clearBtn) clearBtn.classList.add("hidden");
    buylistSearchResults = [];

    if (resultsContainer) {
        resultsContainer.innerHTML = `
            <div id="single-search-empty-slate" class="bg-[#1B2230] border border-[#263245] p-8 text-center font-mono">
                <div class="w-10 h-10 bg-[#10141D] border border-[#263245] flex items-center justify-center mx-auto text-[#00CED1] text-lg font-bold">&para;</div>
                <h3 class="text-sm font-heading font-bold text-white uppercase tracking-wider mt-3">Ready to Query Buylist</h3>
                <p class="text-xs text-[#94A3B8] max-w-md mx-auto mt-1">
                    Enter any card name above to view all printings, condition grade multipliers, cash payouts, and store credit trade-in values.
                </p>
            </div>
        `;
    }
}

function getMatchingVariant(variants, targetCond = "Lightly Played", targetFinish = "nonfoil") {
    if (!variants || variants.length === 0) return null;
    const condClean = (targetCond || "Lightly Played").toLowerCase().trim();
    const isFoil = (targetFinish || "nonfoil").toLowerCase().includes("foil");

    // Exact condition and finish
    for (const v of variants) {
        const vCond = (v.condition || "").toLowerCase();
        const vFoil = (v.finish || "").toLowerCase().includes("foil");
        if ((vCond.includes(condClean) || (condClean === "lp" && vCond.includes("light")) || (condClean === "nm" && vCond.includes("near"))) && (vFoil === isFoil)) {
            return v;
        }
    }

    // Condition match
    for (const v of variants) {
        const vCond = (v.condition || "").toLowerCase();
        if (vCond.includes(condClean) || (condClean === "lp" && vCond.includes("light")) || (condClean === "nm" && vCond.includes("near"))) {
            return v;
        }
    }

    return variants[0];
}

function renderSingleBuylistResults() {
    const container = document.getElementById("single-search-results");
    if (!container) return;

    if (!buylistSearchResults || buylistSearchResults.length === 0) {
        container.innerHTML = `
            <div class="bg-[#1B2230] border border-[#DC143C]/40 p-6 text-center font-mono text-xs">
                <div class="text-[#FF3358] font-bold uppercase tracking-wider text-sm">// NO BUYLIST MATCHES FOUND</div>
                <p class="text-[#94A3B8] mt-1">Mighty Meeple is not actively purchasing this card on their buylist at this time.</p>
            </div>
        `;
        return;
    }

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    const selectedPayout = document.getElementById("buylist-global-payout")?.value || "credit";

    let html = `
        <div class="flex items-center justify-between font-mono text-xs text-[#94A3B8] px-1">
            <span>Found <strong class="text-white">${buylistSearchResults.length}</strong> buylist printings:</span>
            <span class="text-[#2DD4BF] font-bold">[ Active Default: ${selectedCond} &bull; ${selectedPayout === "credit" ? "Store Credit" : (selectedPayout === "cash" ? "Cash" : "Credit + Cash")} ]</span>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3.5">
    `;

    buylistSearchResults.forEach((card, idx) => {
        const vMatch = getMatchingVariant(card.variants, selectedCond, "nonfoil");
        const creditPrice = vMatch ? vMatch.credit_price : 0.0;
        const cashPrice = vMatch ? vMatch.cash_price : 0.0;
        const sellPrice = vMatch ? vMatch.store_sell_price : 0.0;
        const maxQty = vMatch ? vMatch.max_quantity : 0;
        const cardImg = card.image_url || "/static/img/chimaera_logo.jpg";
        const inBatchQty = getCardCartQty(card.card_name, card.set_name, selectedCond);

        // Condition Pills for quick comparison
        const nmVar = getMatchingVariant(card.variants, "Near Mint", "nonfoil");
        const lpVar = getMatchingVariant(card.variants, "Lightly Played", "nonfoil");
        const mpVar = getMatchingVariant(card.variants, "Moderately Played", "nonfoil");
        const hpVar = getMatchingVariant(card.variants, "Heavily Played", "nonfoil");

        html += `
            <div class="bg-[#1B2230] border border-[#263245] hover:border-[#2DD4BF]/60 p-4 space-y-3 transition flex flex-col justify-between relative group">
                <div class="flex items-start space-x-3.5">
                    <img src="${toScryfallThumb(cardImg)}" alt="${card.card_name}" loading="lazy" decoding="async"
                        class="w-16 h-22 object-cover border border-[#263245] bg-[#10141D] flex-shrink-0"
                        onerror="this.src='/static/img/chimaera_logo.jpg'">
                    <div class="flex-1 min-w-0">
                        <div class="flex items-center space-x-2">
                            <span class="text-[9px] font-mono uppercase px-1 py-0.2 bg-[#2DD4BF]/20 text-[#2DD4BF] border border-[#2DD4BF]/50 font-bold">${card.rarity || 'SINGLE'}</span>
                            <span class="text-[10px] font-mono text-[#94A3B8] truncate">${card.set_name}</span>
                        </div>
                        <h4 class="font-heading font-black text-white text-sm sm:text-base truncate mt-0.5" title="${card.card_name}">
                            ${card.card_name}
                        </h4>
                        
                        <!-- Primary Quoted Payout Block -->
                        <div class="flex items-center space-x-3 mt-2 bg-[#10141D] p-2 border border-[#263245]">
                            ${selectedPayout !== "cash" ? `
                            <div>
                                <span class="text-[9px] font-mono text-[#2DD4BF] uppercase font-bold block">Credit (${selectedCond})</span>
                                <span class="font-mono font-bold text-base text-[#2DD4BF]">$${creditPrice.toFixed(2)}</span>
                            </div>
                            ` : ''}
                            ${selectedPayout !== "credit" ? `
                            <div class="${selectedPayout === 'both' ? 'border-l border-[#263245] pl-3' : ''}">
                                <span class="text-[9px] font-mono text-[#94A3B8] uppercase font-bold block">Cash (${selectedCond})</span>
                                <span class="font-mono font-bold text-base text-white">$${cashPrice.toFixed(2)}</span>
                            </div>
                            ` : ''}
                            <div class="border-l border-[#263245] pl-3 ml-auto text-right">
                                <span class="text-[9px] font-mono text-[#64748B] uppercase block">Sell Price</span>
                                <span class="font-mono text-xs text-[#94A3B8]">$${sellPrice.toFixed(2)}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Condition Quick-Comparison Bar -->
                <div class="grid grid-cols-4 gap-1 font-mono text-[10px] text-center pt-1 border-t border-[#263245]/60">
                    <div class="bg-[#10141D] p-1 border ${selectedCond === 'Near Mint' ? 'border-[#00CED1] bg-[#00CED1]/10' : 'border-[#263245]'}">
                        <span class="text-[#94A3B8] block text-[9px]">NM</span>
                        <span class="text-[#2DD4BF] font-bold">$${nmVar ? nmVar.credit_price.toFixed(2) : '0.00'}</span>
                    </div>
                    <div class="bg-[#10141D] p-1 border ${selectedCond === 'Lightly Played' ? 'border-[#2DD4BF] bg-[#2DD4BF]/10' : 'border-[#263245]'}">
                        <span class="text-[#94A3B8] block text-[9px]">LP ★</span>
                        <span class="text-[#2DD4BF] font-bold">$${lpVar ? lpVar.credit_price.toFixed(2) : '0.00'}</span>
                    </div>
                    <div class="bg-[#10141D] p-1 border ${selectedCond === 'Moderately Played' ? 'border-[#00CED1] bg-[#00CED1]/10' : 'border-[#263245]'}">
                        <span class="text-[#94A3B8] block text-[9px]">MP</span>
                        <span class="text-[#2DD4BF] font-bold">$${mpVar ? mpVar.credit_price.toFixed(2) : '0.00'}</span>
                    </div>
                    <div class="bg-[#10141D] p-1 border ${selectedCond === 'Heavily Played' ? 'border-[#00CED1] bg-[#00CED1]/10' : 'border-[#263245]'}">
                        <span class="text-[#94A3B8] block text-[9px]">HP</span>
                        <span class="text-[#2DD4BF] font-bold">$${hpVar ? hpVar.credit_price.toFixed(2) : '0.00'}</span>
                    </div>
                </div>

                <!-- Action Footer -->
                <div class="flex items-center justify-between pt-2 border-t border-[#263245] text-xs font-mono">
                    <span class="text-[10px] text-[#94A3B8]">
                        Max Purchase Qty: <strong class="text-white">${maxQty > 0 ? maxQty : 'Quota Met'}</strong>
                    </span>
                    <div class="flex items-center space-x-2">
                        <button onclick="openBuylistVariantModal(${idx}, 'single')" class="btn-bridge px-2.5 py-1 text-[11px] font-mono">
                            All Prints (${card.variants ? card.variants.length : 0})
                        </button>
                        <button onclick="addSingleResultToCart(${idx})" class="btn-crimson px-2.5 py-1 text-[11px] font-mono font-bold bg-[#2DD4BF] hover:bg-[#00CED1] text-[#10141D] border-[#2DD4BF] flex items-center space-x-1" title="Add to Trade-In Batch">
                            <span>+ Trade-In</span>
                            ${inBatchQty > 0 ? `<span class="bg-[#10141D] text-[#2DD4BF] px-1 text-[9px] font-black font-mono">(${inBatchQty})</span>` : ''}
                        </button>
                    </div>
                </div>
            </div>
        `;
    });

    html += `</div>`;
    container.innerHTML = html;
}

// -------------------------------------------------------------------------
// Bulk Manifest Processing
// -------------------------------------------------------------------------
function updateBulkBuylistCount() {
    const raw = (document.getElementById("bulk-buylist-input")?.value || "").trim();
    if (!raw) {
        const badge = document.getElementById("bulk-buylist-counter");
        if (badge) badge.textContent = "[ 0 Cards ]";
        return;
    }

    const items = raw.split(/[\n;]+/).map(s => s.trim()).filter(s => s.length > 0);
    const unique = new Set(items.map(s => s.toLowerCase()));
    const badge = document.getElementById("bulk-buylist-counter");
    if (badge) {
        badge.textContent = `[ ${unique.size} Cards Identified ]`;
    }
}

const debouncedUpdateBulkBuylistCount = typeof appDebounce === "function" ? appDebounce(updateBulkBuylistCount, 150) : updateBulkBuylistCount;

function loadSampleBulkBuylist() {
    const sample = "The One Ring; Sol Ring; Sheoldred, the Apocalypse; Ragavan, Nimble Pilferer; Mana Crypt; Force of Will; Rhystic Study; Cyclonic Rift; Esper Sentinel; Demonic Tutor";
    const textarea = document.getElementById("bulk-buylist-input");
    if (textarea) {
        textarea.value = sample;
        updateBulkBuylistCount();
        showToast("Loaded sample 10 card trade-in manifest", "info");
    }
}

function clearBulkBuylist() {
    const textarea = document.getElementById("bulk-buylist-input");
    if (textarea) textarea.value = "";
    updateBulkBuylistCount();
    bulkBuylistQuotes = [];
    bulkBuylistSummary = null;

    const resultsEl = document.getElementById("bulk-buylist-results");
    if (resultsEl) resultsEl.classList.add("hidden");

    const kpiCount = document.getElementById("kpi-buylist-count");
    const kpiCredit = document.getElementById("kpi-buylist-credit");
    const kpiCash = document.getElementById("kpi-buylist-cash");
    if (kpiCount) kpiCount.textContent = "0";
    if (kpiCredit) kpiCredit.textContent = "$0.00";
    if (kpiCash) kpiCash.textContent = "$0.00";
}

async function submitBulkBuylist() {
    const raw = (document.getElementById("bulk-buylist-input")?.value || "").trim();
    if (!raw) {
        showToast("Please enter or paste a list of card names first", "info");
        return;
    }

    const condition = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    const payout = document.getElementById("buylist-global-payout")?.value || "credit";
    const game = document.getElementById("buylist-global-game")?.value || "mtg";

    const loadingEl = document.getElementById("bulk-buylist-loading");
    const resultsEl = document.getElementById("bulk-buylist-results");
    const btnSubmit = document.getElementById("btn-submit-bulk-buylist");

    if (loadingEl) loadingEl.classList.remove("hidden");
    if (resultsEl) resultsEl.classList.add("hidden");
    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.classList.add("opacity-50");
    }

    try {
        const res = await fetch("/api/buylist/bulk", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                raw_input: raw,
                condition: condition,
                payout: payout,
                game: game,
            }),
        });

        let data = null;
        const contentType = res.headers.get("content-type") || "";
        if (contentType.includes("application/json")) {
            try {
                data = await res.json();
            } catch (jsonErr) {
                data = null;
            }
        }
        if (!data) {
            const rawText = await res.text();
            let errText = `Server returned HTTP ${res.status}`;
            if (rawText.includes("<title>") && rawText.includes("</title>")) {
                const match = rawText.match(/<title>([^<]+)<\/title>/i);
                if (match) errText = match[1].trim();
            }
            showToast(`Buylist valuation failed: ${errText}`, "error");
            return;
        }

        if (res.ok && data.quotes) {
            bulkBuylistQuotes = data.quotes;
            bulkBuylistSummary = data.summary;
            renderBulkBuylistResults();
            showToast(`Evaluated ${data.summary.matched_count} cards ($${data.summary.total_credit_value.toFixed(2)} Store Credit)`, "success");
        } else {
            showToast(data.error || "Failed to process bulk buylist manifest", "error");
        }
    } catch (err) {
        console.error("Bulk buylist error:", err);
        showToast("Network error calculating bulk buylist", "error");
    } finally {
        if (loadingEl) loadingEl.classList.add("hidden");
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.classList.remove("opacity-50");
        }
    }
}

function renderBulkBuylistResults() {
    const resultsEl = document.getElementById("bulk-buylist-results");
    const tbody = document.getElementById("bulk-buylist-table-body");
    const summaryBadge = document.getElementById("bulk-report-summary-badge");

    if (!resultsEl || !tbody) return;

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    const selectedPayout = document.getElementById("buylist-global-payout")?.value || "credit";

    let totalCredit = 0.0;
    let totalCash = 0.0;
    let totalSell = 0.0;
    let matchedCount = 0;

    let rowsHtml = "";

    bulkBuylistQuotes.forEach((q, idx) => {
        if (q.matched) {
            matchedCount++;
            // Recompute variant matching if user changed grade
            let cPrice = q.credit_price;
            let kPrice = q.cash_price;
            let sPrice = q.store_sell_price;
            let mQty = q.max_quantity;
            let matchedGrade = q.condition || selectedCond;

            if (q.all_prints && q.all_prints.length > 0) {
                const bestPrint = q.all_prints[0];
                const v = getMatchingVariant(bestPrint.variants, selectedCond, "nonfoil");
                if (v) {
                    cPrice = v.credit_price;
                    kPrice = v.cash_price;
                    sPrice = v.store_sell_price;
                    mQty = v.max_quantity;
                    matchedGrade = v.condition;
                }
            }

            totalCredit += cPrice;
            totalCash += kPrice;
            totalSell += sPrice;

            const inBatchQty = getCardCartQty(q.card_name, q.set_name, matchedGrade);

            rowsHtml += `
                <tr class="hover:bg-[#10141D] transition">
                    <td class="p-3 font-bold text-white flex items-center space-x-2.5">
                        ${q.image_url ? `
                        <img src="${toScryfallThumb(q.image_url)}" alt="" loading="lazy" decoding="async" class="w-8 h-10 object-cover border border-[#263245] bg-[#0C0F17] flex-shrink-0" onerror="this.style.display='none'">
                        ` : ''}
                        <div>
                            <div class="truncate max-w-xs" title="${q.card_name}">${q.card_name}</div>
                            <div class="text-[10px] text-[#94A3B8] font-normal">${q.rarity ? q.rarity.toUpperCase() : ''}</div>
                        </div>
                    </td>
                    <td class="p-3 text-[#94A3B8] truncate max-w-[180px]" title="${q.set_name}">${q.set_name}</td>
                    <td class="p-3">
                        <span class="px-1.5 py-0.5 bg-[#10141D] border border-[#263245] text-white text-[10px] font-bold uppercase">
                            ${matchedGrade}
                        </span>
                    </td>
                    <td class="p-3 text-right font-bold text-sm font-mono text-[#2DD4BF]">
                        $${cPrice.toFixed(2)}
                    </td>
                    <td class="p-3 text-right font-bold text-xs font-mono text-white">
                        $${kPrice.toFixed(2)}
                    </td>
                    <td class="p-3 text-right text-xs font-mono text-[#94A3B8]">
                        $${sPrice.toFixed(2)}
                    </td>
                    <td class="p-3 text-center text-[11px] font-mono">
                        <span class="${mQty > 0 ? 'text-[#00CED1] font-bold' : 'text-[#64748B]'}">
                            ${mQty > 0 ? mQty : '0'}
                        </span>
                    </td>
                    <td class="p-3 text-center space-x-1 whitespace-nowrap">
                        <button onclick="openBuylistVariantModal(${idx}, 'bulk')" class="btn-bridge px-2 py-1 text-[10px]" title="View All Set Printings">
                            Prints
                        </button>
                        <button onclick="addBulkQuoteToCart(${idx})" class="btn-crimson px-2 py-1 text-[10px] bg-[#2DD4BF] hover:bg-[#00CED1] text-[#10141D] border-[#2DD4BF] font-bold" title="Add to Trade-In Batch">
                            + Trade-In ${inBatchQty > 0 ? `(${inBatchQty})` : ''}
                        </button>
                    </td>
                </tr>
            `;
        } else {
            rowsHtml += `
                <tr class="hover:bg-[#10141D] opacity-60">
                    <td class="p-3 font-bold text-[#94A3B8]">${q.requested_name}</td>
                    <td class="p-3 text-[#FF3358] italic text-[11px]" colspan="6">Not found on Mighty Meeple buylist</td>
                    <td class="p-3 text-center">
                        <span class="text-[#64748B] text-[10px]">Unavailable</span>
                    </td>
                </tr>
            `;
        }
    });

    tbody.innerHTML = rowsHtml;

    // Update Totals
    const kpiCount = document.getElementById("kpi-buylist-count");
    const kpiCredit = document.getElementById("kpi-buylist-credit");
    const kpiCash = document.getElementById("kpi-buylist-cash");
    const tableCredit = document.getElementById("bulk-table-total-credit");
    const tableCash = document.getElementById("bulk-table-total-cash");
    const tableSell = document.getElementById("bulk-table-total-sell");

    if (kpiCount) kpiCount.textContent = `${matchedCount} / ${bulkBuylistQuotes.length}`;
    if (kpiCredit) kpiCredit.textContent = `$${totalCredit.toFixed(2)}`;
    if (kpiCash) kpiCash.textContent = `$${totalCash.toFixed(2)}`;
    if (tableCredit) tableCredit.textContent = `$${totalCredit.toFixed(2)}`;
    if (tableCash) tableCash.textContent = `$${totalCash.toFixed(2)}`;
    if (tableSell) tableSell.textContent = `$${totalSell.toFixed(2)}`;

    if (summaryBadge) {
        summaryBadge.textContent = `${matchedCount} / ${bulkBuylistQuotes.length} Cards Quoted ($${totalCredit.toFixed(2)} Credit)`;
    }

    resultsEl.classList.remove("hidden");
}

// -------------------------------------------------------------------------
// Variant Modal & Drawer
// -------------------------------------------------------------------------
function openBuylistVariantModal(idx, source = "single") {
    const modal = document.getElementById("modal-buylist-variants");
    const nameEl = document.getElementById("modal-variant-card-name");
    const contentEl = document.getElementById("modal-variant-content");

    if (!modal || !contentEl) return;

    let targetCard = null;
    let allPrints = [];

    if (source === "single") {
        targetCard = buylistSearchResults[idx];
        allPrints = targetCard ? [targetCard] : [];
    } else {
        const quote = bulkBuylistQuotes[idx];
        if (quote) {
            targetCard = quote;
            allPrints = quote.all_prints || [];
        }
    }

    if (!targetCard) return;

    if (nameEl) nameEl.textContent = `${targetCard.card_name} — All Buylist Quotes`;

    let html = "";
    allPrints.forEach(print => {
        const printImg = print.image_url || "";
        html += `
            <div class="bg-[#10141D] border border-[#263245] p-3 space-y-2">
                <div class="flex items-center justify-between border-b border-[#263245] pb-2">
                    <div>
                        <span class="text-white font-bold">${print.card_name}</span>
                        <span class="text-[#94A3B8] text-[11px] block">${print.set_name} &bull; ${print.rarity ? print.rarity.toUpperCase() : 'SINGLE'}</span>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left font-mono text-[11px]">
                        <thead class="text-[#94A3B8] border-b border-[#263245]/60 text-[9px] uppercase">
                            <tr>
                                <th class="py-1">Condition</th>
                                <th class="py-1">Finish</th>
                                <th class="py-1 text-right text-[#2DD4BF]">Store Credit</th>
                                <th class="py-1 text-right">Cash Buy</th>
                                <th class="py-1 text-right text-[#94A3B8]">Retail Sell</th>
                                <th class="py-1 text-center">Max Qty</th>
                                <th class="py-1 text-center">Action</th>
                            </tr>
                        </thead>
                        <tbody class="divide-y divide-[#263245]/40">
        `;

        (print.variants || []).forEach(v => {
            html += `
                <tr class="hover:bg-[#1B2230]">
                    <td class="py-1.5 font-bold ${v.condition === 'Near Mint' || v.condition === 'Lightly Played' ? 'text-white' : 'text-[#94A3B8]'}">
                        ${v.condition}
                    </td>
                    <td class="py-1.5 text-[#94A3B8]">${v.finish}</td>
                    <td class="py-1.5 text-right font-bold text-[#2DD4BF]">$${v.credit_price.toFixed(2)}</td>
                    <td class="py-1.5 text-right font-bold text-white">$${v.cash_price.toFixed(2)}</td>
                    <td class="py-1.5 text-right text-[#94A3B8]">$${v.store_sell_price.toFixed(2)}</td>
                    <td class="py-1.5 text-center text-[#00CED1] font-bold">${v.max_quantity}</td>
                    <td class="py-1.5 text-center">
                        <button onclick="addVariantToCart('${print.card_name.replace(/'/g, "\\'")}', '${print.set_name.replace(/'/g, "\\'")}', '${v.condition}', '${v.finish}', ${v.credit_price}, ${v.cash_price}, ${v.store_sell_price}, '${printImg}')" class="btn-bridge px-2 py-0.5 text-[10px] text-[#2DD4BF] border-[#2DD4BF]/40 hover:bg-[#2DD4BF]/20 font-bold">
                            + Add
                        </button>
                    </td>
                </tr>
            `;
        });

        html += `
                        </tbody>
                    </table>
                </div>
            </div>
        `;
    });

    contentEl.innerHTML = html;
    modal.classList.remove("hidden");
    lockBody();
}

function closeBuylistVariantModal() {
    const modal = document.getElementById("modal-buylist-variants");
    if (modal) {
        modal.classList.add("hidden");
        unlockBody();
    }
}

// -------------------------------------------------------------------------
// CSV Export & Clipboard Copy
// -------------------------------------------------------------------------
function exportBulkBuylistCSV() {
    if (!bulkBuylistQuotes || bulkBuylistQuotes.length === 0) {
        showToast("No buylist quotes available to export", "info");
        return;
    }

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";

    let csvContent = "\uFEFF"; // UTF-8 BOM
    csvContent += "Card Name,Set,Condition Grade,Finish,Store Credit ($),Cash Buy ($),Retail Sell ($),Max Purchase Quantity,Status\n";

    bulkBuylistQuotes.forEach(q => {
        if (q.matched) {
            let cPrice = q.credit_price;
            let kPrice = q.cash_price;
            let sPrice = q.store_sell_price;
            let mQty = q.max_quantity;
            let cond = q.condition || selectedCond;

            if (q.all_prints && q.all_prints.length > 0) {
                const v = getMatchingVariant(q.all_prints[0].variants, selectedCond, "nonfoil");
                if (v) {
                    cPrice = v.credit_price;
                    kPrice = v.cash_price;
                    sPrice = v.store_sell_price;
                    mQty = v.max_quantity;
                    cond = v.condition;
                }
            }

            const cleanName = `"${(q.card_name || '').replace(/"/g, '""')}"`;
            const cleanSet = `"${(q.set_name || '').replace(/"/g, '""')}"`;
            csvContent += `${cleanName},${cleanSet},${cond},${q.finish || 'Normal'},${cPrice.toFixed(2)},${kPrice.toFixed(2)},${sPrice.toFixed(2)},${mQty},Quoted\n`;
        } else {
            const cleanName = `"${(q.requested_name || '').replace(/"/g, '""')}"`;
            csvContent += `${cleanName},Not Found,${selectedCond},Nonfoil,0.00,0.00,0.00,0,Not On Buylist\n`;
        }
    });

    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `mightymeeple_buylist_valuation_${new Date().toISOString().slice(0, 10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showToast("Downloaded Buylist Valuation CSV", "success");
}

function copyBulkBuylistSummary() {
    if (!bulkBuylistQuotes || bulkBuylistQuotes.length === 0) {
        showToast("No buylist quotes to copy", "info");
        return;
    }

    const selectedCond = document.getElementById("buylist-global-condition")?.value || "Lightly Played";
    let summaryText = `MIGHTY MEEPLE BUYLIST VALUATION\n`;
    summaryText += `Grade Default: ${selectedCond}\n`;
    summaryText += `Generated: ${formatESTDate(new Date())}\n`;
    summaryText += `----------------------------------------\n`;

    let totalCredit = 0.0;
    let totalCash = 0.0;

    bulkBuylistQuotes.forEach(q => {
        if (q.matched) {
            let cPrice = q.credit_price;
            let kPrice = q.cash_price;
            if (q.all_prints && q.all_prints.length > 0) {
                const v = getMatchingVariant(q.all_prints[0].variants, selectedCond, "nonfoil");
                if (v) {
                    cPrice = v.credit_price;
                    kPrice = v.cash_price;
                }
            }
            totalCredit += cPrice;
            totalCash += kPrice;
            summaryText += `• ${q.card_name} [${q.set_name}] (${selectedCond}) -> Store Credit: $${cPrice.toFixed(2)} | Cash: $${kPrice.toFixed(2)}\n`;
        } else {
            summaryText += `• ${q.requested_name} -> Not on buylist\n`;
        }
    });

    summaryText += `----------------------------------------\n`;
    summaryText += `TOTAL STORE CREDIT: $${totalCredit.toFixed(2)}\n`;
    summaryText += `TOTAL CASH PAYOUT:  $${totalCash.toFixed(2)}\n`;

    navigator.clipboard.writeText(summaryText).then(() => {
        showToast("Copied Buylist Valuation Summary to clipboard", "success");
    }).catch(() => {
        showToast("Failed to copy to clipboard", "error");
    });
}

// -------------------------------------------------------------------------
// File Drag and Drop / Upload Support
// -------------------------------------------------------------------------
function handleBuylistFileUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    readManifestFile(file);
}

function readManifestFile(file) {
    const reader = new FileReader();
    reader.onload = (e) => {
        const text = e.target.result;
        if (!text) return;

        const lines = text.split(/\r?\n/);
        const cardNames = [];

        for (let line of lines) {
            line = line.trim();
            if (!line) continue;
            // Skip common CSV headers
            const lower = line.toLowerCase();
            if (lower.startsWith("card name") || lower.startsWith("name,") || lower.startsWith("count,") || lower.startsWith("quantity,")) {
                continue;
            }

            // If CSV row, extract first or second column
            if (line.includes(",")) {
                const parts = line.split(",");
                // If first part is a number (e.g. Moxfield "1,Sol Ring"), take second part
                if (/^\d+$/.test(parts[0].trim()) && parts.length > 1) {
                    cardNames.push(parts[1].trim().replace(/^"|"$/g, ''));
                } else {
                    cardNames.push(parts[0].trim().replace(/^"|"$/g, ''));
                }
            } else {
                cardNames.push(line.replace(/^"|"$/g, ''));
            }
        }

        const textarea = document.getElementById("bulk-buylist-input");
        if (textarea) {
            textarea.value = cardNames.join("; ");
            updateBulkBuylistCount();
            showToast(`Loaded ${cardNames.length} cards from ${file.name}`, "success");
        }
    };
    reader.readAsText(file);
}

// Setup Dropzone Drag-and-Drop & Cart Initialization
document.addEventListener("DOMContentLoaded", () => {
    initBuylistCart();

    const dropzone = document.getElementById("buylist-dropzone");
    const fileInput = document.getElementById("buylist-file-input");

    if (dropzone && fileInput) {
        dropzone.addEventListener("click", () => fileInput.click());

        ["dragenter", "dragover"].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.add("drag-active");
            }, false);
        });

        ["dragleave", "drop"].forEach(eventName => {
            dropzone.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzone.classList.remove("drag-active");
            }, false);
        });

        dropzone.addEventListener("drop", (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files && files.length > 0) {
                readManifestFile(files[0]);
            }
        });
    }
});
