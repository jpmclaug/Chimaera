// =========================================================================
// CHIMAERA // TACTICAL INTELLIGENCE CLIENT TELEMETRY & CONTROLS
// =========================================================================

let currentPrintsData = [];
let autocompleteTimeout = null;
let currentPriceIntel = { market: null, great: null, good: null, fair: null };
let editPriceIntel = { market: null, great: null, good: null, fair: null };
let activeViewMode = "grid";
const swipeTrackState = {
    "registry-swipe-track": { index: 0 },
    "deals-swipe-track": { index: 0 }
};

// =========================================================================
// Tactical Timezone Formatter (Eastern Time)
// =========================================================================
function formatESTDate(dateInput, includeTime = true) {
    if (!dateInput) return "--";
    try {
        const d = (dateInput instanceof Date) ? dateInput : new Date(dateInput);
        if (isNaN(d.getTime())) return String(dateInput);
        const options = {
            timeZone: "America/New_York",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
            ...(includeTime ? {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false
            } : {})
        };
        return new Intl.DateTimeFormat("en-US", options).format(d) + " EST";
    } catch {
        return String(dateInput);
    }
}

// =========================================================================
// Tactical Toast Notification System
// =========================================================================
function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    let borderColor = "border-[#00CED1]";
    let prefixTag = "[SYS:CONFIRMED]";
    let textColor = "text-[#00CED1]";

    if (type === "error") {
        borderColor = "border-[#DC143C]";
        prefixTag = "[SYS:ALERT]";
        textColor = "text-[#FF3358]";
    } else if (type === "info") {
        borderColor = "border-[#3D4F6B]";
        prefixTag = "[SYS:TELEMETRY]";
        textColor = "text-[#94A3B8]";
    }
    
    toast.className = `bg-[#1B2230] ${borderColor} border text-[#F1F5F9] text-xs px-4 py-3 font-mono flex items-start justify-between space-x-2.5 transition-all duration-200 transform translate-y-2 opacity-0 pointer-events-auto shadow-xl max-w-md`;
    toast.innerHTML = `
        <div class="flex items-start space-x-2 min-w-0">
            <span class="${textColor} font-bold text-xs uppercase tracking-wider flex-shrink-0">${prefixTag}</span>
            <span class="tracking-tight text-xs text-[#F1F5F9] whitespace-pre-wrap">${message}</span>
        </div>
        <button onclick="this.parentElement.remove()" class="text-[#94A3B8] hover:text-white text-sm leading-none ml-2 flex-shrink-0">&times;</button>
    `;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
    });

    // ONLY auto-remove non-error toasts; keep error alerts visible until dismissed
    if (type !== "error") {
        setTimeout(() => {
            if (toast.parentElement) {
                toast.classList.add("opacity-0", "translate-y-2");
                setTimeout(() => toast.remove(), 250);
            }
        }, 4500);
    }
}

// =========================================================================
// Modal Controls
// =========================================================================
function openAddCardModal() {
    const modal = document.getElementById("modal-add-card");
    if (modal) {
        modal.classList.remove("hidden");
        document.getElementById("card-search-input").focus();
    }
}

function setModalTag(inputId, tagName) {
    const input = document.getElementById(inputId);
    if (input) {
        input.value = tagName;
        input.focus();
    }
}

function filterByTag(tagName) {
    const filter = document.getElementById("watchlist-filter-tag");
    const tagVal = (tagName || "").toLowerCase().trim();
    if (filter) {
        filter.value = tagVal;
    }
    syncTagPillHighlight(".tag-pill-btn", tagVal);
    filterWatchlist();
}

function syncTagDropdownToPills(tagValue) {
    syncTagPillHighlight(".tag-pill-btn", tagValue);
    filterWatchlist();
}

function filterDealsByTag(tagName) {
    const filter = document.getElementById("deals-filter-tag");
    const tagVal = (tagName || "").toLowerCase().trim();
    if (filter) {
        filter.value = tagVal;
    }
    syncTagPillHighlight(".deals-tag-pill", tagVal);
    filterDeals();
}

function syncDealsTagDropdownToPills(tagValue) {
    syncTagPillHighlight(".deals-tag-pill", tagValue);
    filterDeals();
}

function syncTagPillHighlight(pillSelector, tagValue) {
    const pills = document.querySelectorAll(pillSelector);
    const cleanTag = (tagValue || "").toLowerCase().trim();
    pills.forEach(p => {
        const pillTag = (p.dataset.tag || "").toLowerCase().trim();
        if (pillTag === cleanTag || (!cleanTag && pillTag === "all") || (cleanTag === "all" && pillTag === "all")) {
            p.classList.add("active");
        } else {
            p.classList.remove("active");
        }
    });
}

function closeAddCardModal() {
    const modal = document.getElementById("modal-add-card");
    if (modal) {
        modal.classList.add("hidden");
        document.getElementById("card-search-input").value = "";
        const tagInput = document.getElementById("card-tag-input");
        if (tagInput) tagInput.value = "";
        document.getElementById("autocomplete-dropdown").classList.add("hidden");
        document.getElementById("print-selector-container").classList.add("hidden");
        document.getElementById("btn-submit-add-card").disabled = true;
        currentPrintsData = [];
        currentPriceIntel = { market: null, great: null, good: null, fair: null };
    }
}

function openBulkAddModal() {
    const modal = document.getElementById("modal-bulk-add");
    if (modal) {
        modal.classList.remove("hidden");
        const textarea = document.getElementById("bulk-cards-input");
        if (textarea) {
            textarea.focus();
            updateBulkCount();
        }
    }
}

function closeBulkAddModal() {
    const modal = document.getElementById("modal-bulk-add");
    if (modal) {
        modal.classList.add("hidden");
        const tagInput = document.getElementById("bulk-tag-input");
        if (tagInput) tagInput.value = "";
        const progressBox = document.getElementById("bulk-progress-box");
        if (progressBox) progressBox.classList.add("hidden");
        const submitBtn = document.getElementById("btn-submit-bulk-add");
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = "<span>Register Targets</span>";
        }
    }
}

function switchToBulkAddModal() {
    closeAddCardModal();
    openBulkAddModal();
}

function switchToSingleAddModal() {
    closeBulkAddModal();
    openAddCardModal();
}

function parseCardNamesFromText(text) {
    if (!text) return [];
    const lines = text.split(/\r?\n/);
    const rawNames = [];
    lines.forEach(line => {
        if (!line.trim()) return;
        const parts = line.split(";");
        parts.forEach(p => {
            const cleaned = p.trim().replace(/^["']|["']$/g, "").trim();
            if (cleaned) rawNames.push(cleaned);
        });
    });
    const seen = new Set();
    const unique = [];
    rawNames.forEach(n => {
        const lower = n.toLowerCase();
        if (!seen.has(lower)) {
            seen.add(lower);
            unique.push(n);
        }
    });
    return unique;
}

function updateBulkCount() {
    const textarea = document.getElementById("bulk-cards-input");
    const badge = document.getElementById("bulk-counter-badge");
    if (!textarea || !badge) return;

    const names = parseCardNamesFromText(textarea.value);
    const count = names.length;
    badge.textContent = `[ ${count} Target${count === 1 ? '' : 's'} Identified ]`;
    if (count > 0) {
        badge.className = "text-[11px] font-mono text-[#00CED1] font-bold bg-[#10141D] px-2 py-0.5 border border-[#00CED1]/40";
    } else {
        badge.className = "text-[11px] font-mono text-[#94A3B8] font-bold bg-[#10141D] px-2 py-0.5 border border-[#263245]";
    }
}

function toggleBulkCustomTargetInput() {
    const strategySelect = document.getElementById("bulk-target-strategy-select");
    const container = document.getElementById("bulk-custom-price-container");
    if (strategySelect && container) {
        if (strategySelect.value === "custom") {
            container.classList.remove("hidden");
            document.getElementById("bulk-custom-target-input")?.focus();
        } else {
            container.classList.add("hidden");
        }
    }
}

async function openEditTargetModal(id, name, currentTarget, notifyMM = true, isAnyVersion = true, currentTag = "") {
    const modal = document.getElementById("modal-edit-target");
    if (!modal) return;

    document.getElementById("edit-target-item-id").value = id;
    document.getElementById("edit-target-card-name").textContent = `TARGET: ${name}`;
    document.getElementById("edit-target-price-input").value = currentTarget !== null ? currentTarget : "";
    
    const tagInput = document.getElementById("edit-target-tag-input");
    if (tagInput) {
        tagInput.value = currentTag || "";
    }

    const scopeElem = document.getElementById("edit-target-scope-label");
    if (scopeElem) {
        scopeElem.textContent = `Surveillance Scope: ${isAnyVersion ? 'Any Version (Card in General)' : 'Specific Printing'}`;
    }

    const mmCheckbox = document.getElementById("edit-target-mm-alert");
    if (mmCheckbox) {
        mmCheckbox.checked = Boolean(notifyMM);
    }

    modal.classList.remove("hidden");
    document.getElementById("edit-target-price-input").focus();

    // Fetch price intel for quick presets in edit modal
    try {
        const res = await fetch(`/api/card/price-intel?name=${encodeURIComponent(name)}`);
        if (res.ok) {
            const data = await res.json();
            const mp = data.market_price;
            if (mp && mp > 0) {
                editPriceIntel = {
                    market: mp,
                    great: data.targets.great_deal_20,
                    good: data.targets.good_deal_10,
                    fair: data.targets.fair_market,
                };
                const gLabel = document.getElementById("label-edit-preset-great");
                const gdLabel = document.getElementById("label-edit-preset-good");
                const mLabel = document.getElementById("label-edit-preset-market");
                if (gLabel && editPriceIntel.great) gLabel.textContent = `-20% ($${editPriceIntel.great.toFixed(2)})`;
                if (gdLabel && editPriceIntel.good) gdLabel.textContent = `-10% ($${editPriceIntel.good.toFixed(2)})`;
                if (mLabel && editPriceIntel.fair) mLabel.textContent = `100% ($${editPriceIntel.fair.toFixed(2)})`;
            }
        }
    } catch (e) {
        console.debug("Failed to fetch price intel for edit modal:", e);
    }
}

function closeEditTargetModal() {
    const modal = document.getElementById("modal-edit-target");
    if (modal) modal.classList.add("hidden");
}

// =========================================================================
// Price Intelligence Presets
// =========================================================================
function applyTargetPreset(type) {
    const input = document.getElementById("card-target-price-input");
    if (!input || !currentPriceIntel.market) return;

    if (type === "great" && currentPriceIntel.great !== null) {
        input.value = currentPriceIntel.great.toFixed(2);
    } else if (type === "good" && currentPriceIntel.good !== null) {
        input.value = currentPriceIntel.good.toFixed(2);
    } else if (type === "market" && currentPriceIntel.fair !== null) {
        input.value = currentPriceIntel.fair.toFixed(2);
    }
}

function applyEditTargetPreset(type) {
    const input = document.getElementById("edit-target-price-input");
    if (!input || !editPriceIntel.market) return;

    if (type === "great" && editPriceIntel.great !== null) {
        input.value = editPriceIntel.great.toFixed(2);
    } else if (type === "good" && editPriceIntel.good !== null) {
        input.value = editPriceIntel.good.toFixed(2);
    } else if (type === "market" && editPriceIntel.fair !== null) {
        input.value = editPriceIntel.fair.toFixed(2);
    }
}

function updatePriceIntelPresets(marketPrice) {
    const marketLabel = document.getElementById("intel-market-price");
    const labelGreat = document.getElementById("label-preset-great");
    const labelGood = document.getElementById("label-preset-good");
    const labelMarket = document.getElementById("label-preset-market");

    if (!marketPrice || marketPrice <= 0) {
        currentPriceIntel = { market: null, great: null, good: null, fair: null };
        if (marketLabel) marketLabel.textContent = "Estimating...";
        if (labelGreat) labelGreat.textContent = "-20%";
        if (labelGood) labelGood.textContent = "-10%";
        if (labelMarket) labelMarket.textContent = "100%";
        return;
    }

    const great = Math.round(marketPrice * 0.80 * 100) / 100;
    const good = Math.round(marketPrice * 0.90 * 100) / 100;
    const fair = Math.round(marketPrice * 100) / 100;

    currentPriceIntel = {
        market: marketPrice,
        great: great,
        good: good,
        fair: fair,
    };

    if (marketLabel) marketLabel.textContent = `$${fair.toFixed(2)}`;
    if (labelGreat) labelGreat.textContent = `-20% ($${great.toFixed(2)})`;
    if (labelGood) labelGood.textContent = `-10% ($${good.toFixed(2)})`;
    if (labelMarket) labelMarket.textContent = `100% ($${fair.toFixed(2)})`;
}

// =========================================================================
function getLowestPriceAcrossPrints(prints, finish) {
    if (!prints || prints.length === 0) return null;
    const finishKey = finish === "foil" ? "usd_foil" : (finish === "etched" ? "usd_etched" : "usd");
    let prices = [];
    prints.forEach(p => {
        if (!p.prices) return;
        const val = p.prices[finishKey] || (finish === "nonfoil" ? p.prices.usd : null);
        if (val) {
            const num = parseFloat(val);
            if (!isNaN(num) && num > 0) {
                prices.push(num);
            }
        }
    });
    if (prices.length === 0) {
        prints.forEach(p => {
            if (!p.prices) return;
            [p.prices.usd, p.prices.usd_foil, p.prices.usd_etched].forEach(v => {
                if (v) {
                    const num = parseFloat(v);
                    if (!isNaN(num) && num > 0) prices.push(num);
                }
            });
        });
    }
    return prices.length > 0 ? Math.min(...prices) : null;
}

// =========================================================================
// Autocomplete & Print Selection
// =========================================================================
document.addEventListener("DOMContentLoaded", () => {
    const searchInput = document.getElementById("card-search-input");
    const dropdown = document.getElementById("autocomplete-dropdown");
    const printSelect = document.getElementById("card-print-select");
    const finishSelect = document.getElementById("card-finish-select");

    if (searchInput) {
        searchInput.addEventListener("input", (e) => {
            const query = e.target.value.trim();
            clearTimeout(autocompleteTimeout);

            if (query.length < 2) {
                dropdown.classList.add("hidden");
                dropdown.innerHTML = "";
                return;
            }

            autocompleteTimeout = setTimeout(async () => {
                try {
                    const res = await fetch(`/api/scryfall/autocomplete?q=${encodeURIComponent(query)}`);
                    const data = await res.json();
                    const suggestions = data.suggestions || [];

                    if (suggestions.length === 0) {
                        dropdown.innerHTML = `<div class="p-3 text-xs font-mono text-[#94A3B8] text-center uppercase font-medium">[ NO MATCHING TARGETS IDENTIFIED ]</div>`;
                    } else {
                        dropdown.innerHTML = suggestions.map(name => `
                            <div class="px-3.5 py-2.5 text-xs font-mono text-[#F1F5F9] hover:bg-[#222B3D] hover:text-[#00CED1] cursor-pointer transition flex items-center justify-between border-b border-[#263245]/60"
                                 onclick="selectCardName('${name.replace(/'/g, "\\'")}')">
                                <span>${name}</span>
                                <span class="text-xs text-[#00CED1] font-bold">&rarr;</span>
                            </div>
                        `).join("");
                    }
                    dropdown.classList.remove("hidden");
                } catch (err) {
                    console.error("Autocomplete error:", err);
                }
            }, 250);
        });

        searchInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                e.preventDefault();
                const firstSuggestion = dropdown.querySelector(".cursor-pointer");
                if (firstSuggestion && !dropdown.classList.contains("hidden")) {
                    firstSuggestion.click();
                } else if (searchInput.value.trim().length >= 2) {
                    selectCardName(searchInput.value.trim());
                }
            }
        });
    }

    if (printSelect) {
        printSelect.addEventListener("change", () => {
            updateSelectedPrintView();
        });
    }

    if (finishSelect) {
        finishSelect.addEventListener("change", () => {
            updateSelectedPrintView();
        });
    }

    // Close autocomplete on click outside
    document.addEventListener("click", (e) => {
        if (searchInput && dropdown && !searchInput.contains(e.target) && !dropdown.contains(e.target)) {
            dropdown.classList.add("hidden");
        }
    });

    // Close modals on backdrop click
    const modalIds = ["modal-add-card", "modal-bulk-add", "modal-edit-target", "modal-cadence-settings", "modal-buylist-variants"];
    modalIds.forEach(id => {
        const modal = document.getElementById(id);
        if (modal) {
            modal.addEventListener("click", (e) => {
                if (e.target === modal) {
                    if (id === "modal-add-card") closeAddCardModal();
                    else if (id === "modal-bulk-add") closeBulkAddModal();
                    else if (id === "modal-edit-target") closeEditTargetModal();
                    else if (id === "modal-cadence-settings") closeCadenceModal();
                    else if (id === "modal-buylist-variants" && typeof closeBuylistVariantModal === "function") closeBuylistVariantModal();
                }
            });
        }
    });

    // Close modals on Escape key
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            closeAddCardModal();
            closeBulkAddModal();
            closeEditTargetModal();
            closeCadenceModal();
            if (typeof closeBuylistVariantModal === "function") closeBuylistVariantModal();
            const logoMenu = document.getElementById("logo-dropdown-menu");
            if (logoMenu && !logoMenu.classList.contains("hidden")) {
                logoMenu.classList.add("hidden");
            }
        }
    });
});

async function selectCardName(cardName) {
    const searchInput = document.getElementById("card-search-input");
    const dropdown = document.getElementById("autocomplete-dropdown");
    const printContainer = document.getElementById("print-selector-container");
    const printSelect = document.getElementById("card-print-select");
    const submitBtn = document.getElementById("btn-submit-add-card");

    if (searchInput) searchInput.value = cardName;
    if (dropdown) dropdown.classList.add("hidden");

    // Fetch Prints
    try {
        const res = await fetch(`/api/scryfall/prints?name=${encodeURIComponent(cardName)}`);
        const data = await res.json();
        currentPrintsData = data.prints || [];

        if (currentPrintsData.length === 0) {
            showToast("No prints found for target query", "error");
            return;
        }

        // Populate print selector with 'any' as the default first option!
        const printOptions = currentPrintsData.map(p => `
            <option value="${p.id}">
                ${p.set_name} (${p.set_code.toUpperCase()}) #${p.collector_number} [${p.rarity.toUpperCase()}] - ${p.released_at ? p.released_at.substring(0, 4) : ''}
            </option>
        `).join("");

        printSelect.innerHTML = `
            <option value="any" selected>★ Any Version (Card in General - Lowest Market Price)</option>
            ${printOptions}
        `;

        printContainer.classList.remove("hidden");
        submitBtn.disabled = false;

        // Render "Any Version" view by default
        updateSelectedPrintView();

    } catch (err) {
        console.error("Error loading prints:", err);
        showToast("Failed to load printing classifications", "error");
    }
}

function updateSelectedPrintView() {
    const printSelect = document.getElementById("card-print-select");
    const finishSelect = document.getElementById("card-finish-select");
    const imgElem = document.getElementById("card-preview-img");
    const titleElem = document.getElementById("card-preview-title");
    const metaElem = document.getElementById("card-preview-meta");
    const priceElem = document.getElementById("card-preview-price");
    const scopeIndicator = document.getElementById("scope-badge-indicator");

    if (!currentPrintsData || currentPrintsData.length === 0) return;

    const selectedValue = printSelect ? printSelect.value : "any";
    const selectedFinish = finishSelect ? finishSelect.value : "nonfoil";

    if (selectedValue === "any") {
        // Any Version Mode
        const canonical = currentPrintsData[0];
        if (scopeIndicator) {
            scopeIndicator.textContent = "[ ★ Any Version (General Card) ]";
            scopeIndicator.className = "text-[10px] font-mono text-[#00CED1] font-bold uppercase tracking-wider";
        }
        if (imgElem) {
            imgElem.src = canonical.image_uri || "";
            imgElem.alt = canonical.name;
        }
        if (titleElem) titleElem.textContent = canonical.name;
        if (metaElem) metaElem.textContent = `All Printings (${currentPrintsData.length} editions) // Surveillance: Any Version`;

        // Enable all finish options
        Array.from(finishSelect.options).forEach(opt => {
            opt.disabled = false;
            opt.textContent = opt.value === "nonfoil" ? "Non-foil" : (opt.value === "foil" ? "Foil" : "Etched Foil");
        });

        const lowest = getLowestPriceAcrossPrints(currentPrintsData, selectedFinish);
        if (priceElem) {
            priceElem.textContent = lowest !== null ? `TCGPLAYER MARKET TELEMETRY: $${lowest.toFixed(2)} (Lowest Print)` : "TCGPLAYER MARKET TELEMETRY: Market Active";
        }

        updatePriceIntelPresets(lowest);

    } else {
        // Specific Print Mode
        const printObj = currentPrintsData.find(p => p.id === selectedValue);
        if (!printObj) return;

        if (scopeIndicator) {
            scopeIndicator.textContent = `[ 🎯 ${printObj.set_code.toUpperCase()} #${printObj.collector_number} ]`;
            scopeIndicator.className = "text-[10px] font-mono text-[#2DD4BF] font-bold uppercase tracking-wider";
        }

        if (imgElem) {
            imgElem.src = printObj.image_uri || "";
            imgElem.alt = printObj.name;
        }
        if (titleElem) titleElem.textContent = printObj.name;
        if (metaElem) metaElem.textContent = `${printObj.set_name} (${printObj.set_code.toUpperCase()}) #${printObj.collector_number} // ${printObj.rarity.toUpperCase()}`;

        // Update finish dropdown availability for specific print
        const availableFinishes = printObj.finishes || ["nonfoil"];
        Array.from(finishSelect.options).forEach(opt => {
            if (availableFinishes.includes(opt.value)) {
                opt.disabled = false;
                opt.textContent = opt.value === "nonfoil" ? "Non-foil" : (opt.value === "foil" ? "Foil" : "Etched Foil");
            } else {
                opt.disabled = true;
                opt.textContent = `${opt.value.toUpperCase()} (UNAVAILABLE)`;
            }
        });

        if (!availableFinishes.includes(finishSelect.value)) {
            finishSelect.value = availableFinishes[0];
        }

        const finishVal = finishSelect.value;
        let estPrice = "N/A";
        let numericPrice = null;
        if (finishVal === "foil" && printObj.prices.usd_foil) {
            estPrice = `$${printObj.prices.usd_foil}`;
            numericPrice = parseFloat(printObj.prices.usd_foil);
        } else if (finishVal === "etched" && printObj.prices.usd_etched) {
            estPrice = `$${printObj.prices.usd_etched}`;
            numericPrice = parseFloat(printObj.prices.usd_etched);
        } else if (printObj.prices.usd) {
            estPrice = `$${printObj.prices.usd}`;
            numericPrice = parseFloat(printObj.prices.usd);
        }

        if (priceElem) priceElem.textContent = `TCGPLAYER MARKET TELEMETRY: ${estPrice}`;
        updatePriceIntelPresets(numericPrice);
    }
}

// =========================================================================
// Add Card Submit Action
// =========================================================================
async function submitAddCard() {
    const printSelect = document.getElementById("card-print-select");
    const finishSelect = document.getElementById("card-finish-select");
    const targetPriceInput = document.getElementById("card-target-price-input");
    const notifyMMCheckbox = document.getElementById("card-notify-mm-input");
    const tagInput = document.getElementById("card-tag-input");
    const submitBtn = document.getElementById("btn-submit-add-card");

    if (!currentPrintsData || currentPrintsData.length === 0) {
        showToast("Search and select a card first.", "error");
        return;
    }

    const selectedValue = printSelect ? printSelect.value : "any";
    const tagValue = (tagInput ? tagInput.value : "").trim() || null;
    let payload = {};

    if (selectedValue === "any") {
        const canonical = currentPrintsData[0];
        payload = {
            name: canonical.name,
            is_any_version: true,
            scryfall_id: canonical.id,
            set_code: null,
            collector_number: null,
            image_uri: canonical.image_uri,
            finish: finishSelect.value,
            target_price: targetPriceInput.value ? parseFloat(targetPriceInput.value) : null,
            notify_mm_stock: notifyMMCheckbox ? notifyMMCheckbox.checked : true,
            tag: tagValue,
        };
    } else {
        const printObj = currentPrintsData.find(p => p.id === selectedValue);
        if (!printObj) {
            showToast("Select a valid target printing.", "error");
            return;
        }
        payload = {
            name: printObj.name,
            is_any_version: false,
            scryfall_id: printObj.id,
            set_code: printObj.set_code,
            collector_number: printObj.collector_number,
            image_uri: printObj.image_uri,
            finish: finishSelect.value,
            target_price: targetPriceInput.value ? parseFloat(targetPriceInput.value) : null,
            notify_mm_stock: notifyMMCheckbox ? notifyMMCheckbox.checked : true,
            tag: tagValue,
        };
    }

    submitBtn.disabled = true;
    submitBtn.textContent = "Scanning Telemetry...";

    try {
        const res = await fetch("/api/watchlist/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Target acquired: ${payload.name}`, "success");
            closeAddCardModal();
            submitBtn.disabled = false;
            submitBtn.textContent = "Add to Watchlist";
            if (!window.location.pathname.includes("/buylist")) {
                setTimeout(() => window.location.reload(), 500);
            }
        } else {
            showToast(data.error || "Failed to register target", "error");
            submitBtn.disabled = false;
            submitBtn.textContent = "Add to Watchlist";
        }
    } catch (err) {
        console.error("Add card error:", err);
        showToast("Communication failure adding target", "error");
        submitBtn.disabled = false;
        submitBtn.textContent = "Add to Watchlist";
    }
}

// =========================================================================
// Bulk Add Submit Action
// =========================================================================
async function submitBulkAdd() {
    const textarea = document.getElementById("bulk-cards-input");
    const finishSelect = document.getElementById("bulk-finish-select");
    const strategySelect = document.getElementById("bulk-target-strategy-select");
    const customTargetInput = document.getElementById("bulk-custom-target-input");
    const notifyMMCheckbox = document.getElementById("bulk-notify-mm-input");
    const tagInput = document.getElementById("bulk-tag-input");
    const submitBtn = document.getElementById("btn-submit-bulk-add");
    const progressBox = document.getElementById("bulk-progress-box");
    const progressText = document.getElementById("bulk-progress-text");
    const progressDetail = document.getElementById("bulk-progress-detail");

    const rawText = textarea ? textarea.value : "";
    const cardNames = parseCardNamesFromText(rawText);

    if (cardNames.length === 0) {
        showToast("Enter at least one card name separated by semicolons (;).", "error");
        if (textarea) textarea.focus();
        return;
    }

    const payload = {
        card_names: cardNames,
        finish: finishSelect ? finishSelect.value : "nonfoil",
        target_strategy: strategySelect ? strategySelect.value : "none",
        target_price: (strategySelect && strategySelect.value === "custom" && customTargetInput?.value) ? parseFloat(customTargetInput.value) : null,
        notify_mm_stock: notifyMMCheckbox ? notifyMMCheckbox.checked : true,
        tag: (tagInput ? tagInput.value : "").trim() || null,
    };

    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `
            <svg class="w-3.5 h-3.5 animate-spin mr-1.5" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"></path>
            </svg>
            <span>Processing Batch (${cardNames.length})...</span>
        `;
    }

    if (progressBox) progressBox.classList.remove("hidden");
    if (progressText) progressText.textContent = `Acquiring ${cardNames.length} Targets via Scryfall Collection...`;
    if (progressDetail) progressDetail.textContent = "Connecting to market telemetry & running real-time vendor price surveillance...";

    try {
        const res = await fetch("/api/watchlist/bulk-add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (res.ok) {
            const added = data.added_count || 0;
            const skipped = data.skipped_count || 0;
            const failed = data.failed_count || 0;

            let toastType = "success";
            if (added === 0 && failed > 0) toastType = "error";
            else if (failed > 0 || skipped > 0) toastType = "info";

            showToast(data.message || `Successfully added ${added} targets!`, toastType);

            if (added > 0) {
                setTimeout(() => window.location.reload(), 900);
            } else {
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.innerHTML = "<span>Register Targets</span>";
                }
                if (progressBox) progressBox.classList.add("hidden");
            }
        } else {
            showToast(data.error || "Failed to process bulk acquisition.", "error");
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = "<span>Register Targets</span>";
            }
            if (progressBox) progressBox.classList.add("hidden");
        }
    } catch (err) {
        console.error("Bulk add error:", err);
        showToast("Communication failure during bulk acquisition.", "error");
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = "<span>Register Targets</span>";
        }
        if (progressBox) progressBox.classList.add("hidden");
    }
}

// =========================================================================
// TCGplayer Purchase Reconciliation Modal & Execution
// =========================================================================
let currentTcgReconciliationData = null;

function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function openTcgPurchaseModal() {
    const modal = document.getElementById("modal-tcgplayer-purchase");
    if (modal) {
        modal.classList.remove("hidden");
        const textarea = document.getElementById("tcg-purchase-input");
        if (textarea) {
            textarea.focus();
            onTcgInputChange();
        }
    }
}

function closeTcgPurchaseModal() {
    const modal = document.getElementById("modal-tcgplayer-purchase");
    if (modal) {
        modal.classList.add("hidden");
        const progressBox = document.getElementById("tcg-progress-box");
        if (progressBox) progressBox.classList.add("hidden");
        const resultsContainer = document.getElementById("tcg-results-container");
        if (resultsContainer) resultsContainer.classList.add("hidden");
        const executeBtn = document.getElementById("btn-submit-tcg-execute");
        if (executeBtn) executeBtn.classList.add("hidden");
        currentTcgReconciliationData = null;
    }
}

function onTcgInputChange() {
    const textarea = document.getElementById("tcg-purchase-input");
    const badge = document.getElementById("tcg-counter-badge");
    if (!textarea || !badge) return;

    const val = textarea.value.trim();
    if (!val) {
        badge.textContent = "[ Ready ]";
        badge.className = "text-[11px] font-mono text-[#94A3B8] font-bold bg-[#10141D] px-2 py-0.5 border border-[#263245]";
        return;
    }
    const lines = val.split(/\r?\n/).filter(l => l.trim() && !l.toLowerCase().startsWith("qty"));
    const count = lines.length;
    badge.textContent = `[ ${count} Line${count === 1 ? '' : 's'} ]`;
    badge.className = "text-[11px] font-mono text-[#00CED1] font-bold bg-[#10141D] px-2 py-0.5 border border-[#00CED1]/40";
}

async function previewTcgPurchase() {
    const textarea = document.getElementById("tcg-purchase-input");
    const text = textarea ? textarea.value.trim() : "";
    if (!text) {
        showToast("Paste a TCGplayer purchase manifest first.", "error");
        return;
    }

    const previewBtn = document.getElementById("btn-tcg-preview");
    const progressBox = document.getElementById("tcg-progress-box");
    const resultsContainer = document.getElementById("tcg-results-container");
    const executeBtn = document.getElementById("btn-submit-tcg-execute");

    if (previewBtn) previewBtn.disabled = true;
    if (progressBox) progressBox.classList.remove("hidden");
    if (resultsContainer) resultsContainer.classList.add("hidden");
    if (executeBtn) executeBtn.classList.add("hidden");

    try {
        const res = await fetch("/api/watchlist/reconcile-purchase/preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ raw_text: text }),
        });
        const data = await res.json();

        if (progressBox) progressBox.classList.add("hidden");
        if (previewBtn) previewBtn.disabled = false;

        if (!res.ok) {
            showToast(data.error || "Failed to parse purchase manifest.", "error");
            return;
        }

        currentTcgReconciliationData = data;
        renderTcgPreviewResults(data);
    } catch (err) {
        console.error("TCGplayer preview error:", err);
        showToast("Communication failure during purchase preview.", "error");
        if (progressBox) progressBox.classList.add("hidden");
        if (previewBtn) previewBtn.disabled = false;
    }
}

function renderTcgPreviewResults(data) {
    const resultsContainer = document.getElementById("tcg-results-container");
    const matchedList = document.getElementById("tcg-matched-list");
    const matchedCountLabel = document.getElementById("tcg-matched-count-label");
    const unmatchedSection = document.getElementById("tcg-unmatched-section");
    const unmatchedList = document.getElementById("tcg-unmatched-list");
    const unmatchedCountLabel = document.getElementById("tcg-unmatched-count-label");
    const executeBtn = document.getElementById("btn-submit-tcg-execute");

    if (!resultsContainer || !matchedList) return;

    matchedList.innerHTML = "";
    const matched = data.matched_targets || [];

    matchedCountLabel.textContent = `${matched.length} Buy Target${matched.length === 1 ? '' : 's'} Matched On Watchlist`;

    if (matched.length === 0) {
        matchedList.innerHTML = `
            <div class="p-3 text-center text-xs font-mono text-[#94A3B8] bg-[#10141D] border border-[#263245]">
                No cards from this purchase matched any active buy targets in your registry.
            </div>
        `;
        if (executeBtn) executeBtn.classList.add("hidden");
    } else {
        matched.forEach(t => {
            const row = document.createElement("div");
            row.className = "flex items-center justify-between p-2 bg-[#10141D] border border-[#263245] hover:border-[#00CED1]/50 text-xs font-mono transition";
            row.id = `tcg-match-row-${t.id}`;

            const tagHtml = t.tag ? `<span class="text-[10px] text-[#C084FC] bg-[#9333EA]/10 border border-[#9333EA]/30 px-1.5 py-0.5 ml-2">🏷️ ${escapeHtml(t.tag)}</span>` : "";
            const targetPriceHtml = t.target_price ? `<span class="text-[10px] text-[#94A3B8]">Target: <strong class="text-white">$${t.target_price.toFixed(2)}</strong></span>` : "";
            const purchasedInfo = `Qty: ${t.purchased_qty}${t.purchased_condition ? ' • ' + t.purchased_condition : ''}${t.purchased_set ? ' • ' + t.purchased_set : ''}`;

            row.innerHTML = `
                <div class="flex items-center space-x-2.5 min-w-0 flex-1">
                    <input type="checkbox" id="tcg-target-check-${t.id}" value="${t.id}" class="tcg-target-checkbox w-4 h-4 text-[#DC143C] rounded-none bg-[#1B2230] border-[#263245] cursor-pointer" checked onchange="updateTcgExecuteButton()">
                    <label for="tcg-target-check-${t.id}" class="cursor-pointer truncate">
                        <span class="font-bold text-white text-xs">${escapeHtml(t.name)}</span>
                        ${tagHtml}
                        <span class="text-[10px] text-[#64748B] block mt-0.5">${escapeHtml(purchasedInfo)}</span>
                    </label>
                </div>
                <div class="text-right flex-shrink-0 ml-2">
                    ${targetPriceHtml}
                </div>
            `;
            matchedList.appendChild(row);
        });

        if (executeBtn) {
            executeBtn.classList.remove("hidden");
            updateTcgExecuteButton();
        }
    }

    // Render unmatched list
    const unmatched = data.unmatched_purchases || [];
    if (unmatched.length > 0 && unmatchedSection && unmatchedList) {
        unmatchedSection.classList.remove("hidden");
        unmatchedCountLabel.textContent = `${unmatched.length} Purchased Item${unmatched.length === 1 ? '' : 's'} Not on Watchlist`;
        unmatchedList.innerHTML = unmatched.map(u => `
            <div class="flex items-center justify-between py-0.5">
                <span class="text-white">${escapeHtml(u.card_name || u.raw_name)}</span>
                <span class="text-[#64748B] text-[10px]">${u.quantity}x • ${escapeHtml(u.set_name || 'N/A')}</span>
            </div>
        `).join("");
    } else if (unmatchedSection) {
        unmatchedSection.classList.add("hidden");
    }

    resultsContainer.classList.remove("hidden");
}

function toggleAllTcgMatches(checked) {
    const checkboxes = document.querySelectorAll(".tcg-target-checkbox");
    checkboxes.forEach(cb => cb.checked = checked);
    updateTcgExecuteButton();
}

function updateTcgExecuteButton() {
    const executeBtn = document.getElementById("btn-submit-tcg-execute");
    if (!executeBtn) return;
    const checkboxes = document.querySelectorAll(".tcg-target-checkbox:checked");
    const count = checkboxes.length;
    executeBtn.disabled = count === 0;
    executeBtn.innerHTML = `<span>De-Register ${count} Selected Target${count === 1 ? '' : 's'}</span>`;
}

function toggleTcgUnmatchedDetails() {
    const list = document.getElementById("tcg-unmatched-list");
    const arrow = document.getElementById("tcg-unmatched-arrow");
    if (!list) return;
    const isHidden = list.classList.contains("hidden");
    if (isHidden) {
        list.classList.remove("hidden");
        if (arrow) arrow.textContent = "▲";
    } else {
        list.classList.add("hidden");
        if (arrow) arrow.textContent = "▼";
    }
}

async function executeTcgPurchaseReconciliation() {
    const checkboxes = document.querySelectorAll(".tcg-target-checkbox:checked");
    const targetIds = Array.from(checkboxes).map(cb => parseInt(cb.value, 10));

    if (targetIds.length === 0) {
        showToast("Select at least one target to de-register.", "error");
        return;
    }

    const count = targetIds.length;
    const addInventory = document.getElementById("tcg-add-inventory-check")?.checked ?? false;

    const confirmMsg = `CONFIRM PURCHASE RECONCILIATION:\nPermanently de-register ${count} target(s) from your buy list?` +
        (addInventory ? `\n\n(Also adding purchased cards to collection/inventory)` : "");

    if (!confirm(confirmMsg)) {
        return;
    }

    const executeBtn = document.getElementById("btn-submit-tcg-execute");
    if (executeBtn) {
        executeBtn.disabled = true;
        executeBtn.innerHTML = `<span>Processing De-registration...</span>`;
    }

    try {
        const payload = {
            target_ids: targetIds,
            add_to_inventory: addInventory,
            purchased_items: currentTcgReconciliationData ? currentTcgReconciliationData.parsed_items : [],
        };

        const res = await fetch("/api/watchlist/reconcile-purchase/execute", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Successfully de-registered ${count} targets.`, "success");
            closeTcgPurchaseModal();

            // Fade out cards in registry if on the watchlist page
            targetIds.forEach(id => {
                const elem = document.getElementById(`card-row-${id}`);
                const compactElem = document.getElementById(`compact-card-${id}`);
                [elem, compactElem].forEach(el => {
                    if (el) {
                        el.style.transition = "opacity 0.3s, transform 0.3s";
                        el.style.opacity = "0";
                        el.style.transform = "scale(0.95)";
                    }
                });
            });

            // Reload page smoothly to refresh all KPIs, tags, and lists
            setTimeout(() => window.location.reload(), 400);
        } else {
            showToast(data.error || "Failed to reconcile purchase.", "error");
            if (executeBtn) {
                executeBtn.disabled = false;
                updateTcgExecuteButton();
            }
        }
    } catch (err) {
        console.error("Execute reconciliation error:", err);
        showToast("Network error executing purchase reconciliation.", "error");
        if (executeBtn) {
            executeBtn.disabled = false;
            updateTcgExecuteButton();
        }
    }
}

// =========================================================================
// Edit Target Price & Alert Settings Submit Action
// =========================================================================
async function submitEditTarget() {
    const itemId = document.getElementById("edit-target-item-id").value;
    const targetPriceVal = document.getElementById("edit-target-price-input").value;
    const mmAlertChecked = document.getElementById("edit-target-mm-alert")?.checked ?? true;
    const tagInput = document.getElementById("edit-target-tag-input");
    const tagVal = (tagInput ? tagInput.value : "").trim();

    const submitBtn = document.getElementById("btn-edit-target-submit");
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = `<span>Saving...</span>`;
    }

    try {
        const res = await fetch(`/api/watchlist/update-target/${itemId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_price: targetPriceVal ? parseFloat(targetPriceVal) : null,
                notify_mm_stock: mmAlertChecked,
                tag: tagVal,
            }),
        });

        const data = await res.json();
        if (res.ok) {
            showToast("Target configuration committed.", "success");
            closeEditTargetModal();
            if (data.card) {
                updateCardInRegistryDom(data.card);
            }
        } else {
            showToast(data.error || "Failed to reconfigure threshold", "error");
        }
    } catch (err) {
        console.error("Update target error:", err);
        showToast("Communication error updating target", "error");
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = `<span>Commit</span>`;
        }
    }
}

/**
 * Updates a card's DOM representations across Grid, Compact, and Swipe views,
 * updates telemetry KPIs, and synchronizes tags in real-time without page reload.
 */
function updateCardInRegistryDom(card) {
    if (!card || !card.id) return;
    const cid = card.id;

    const safeName = (card.name || "").replace(/'/g, "\\'");
    const targetArg = card.target_price !== null && card.target_price !== undefined ? card.target_price : "null";
    const safeTag = (card.tag || "").replace(/'/g, "\\'");
    const editOnclickStr = `openEditTargetModal(${cid}, '${safeName}', ${targetArg}, ${card.notify_mm_stock ? 'true' : 'false'}, ${card.is_any_version ? 'true' : 'false'}, '${safeTag}')`;

    // 1. GRID VIEW UPDATES
    const gridRow = document.getElementById(`card-row-${cid}`);
    if (gridRow) {
        gridRow.dataset.tag = (card.tag || "").toLowerCase().trim();
        gridRow.dataset.isDeal = card.is_deal ? "true" : "false";
        gridRow.dataset.mmStock = card.mm_in_stock ? "true" : "false";

        if (card.is_deal) {
            gridRow.classList.remove("border-[#263245]", "hover:border-[#3D4F6B]");
            gridRow.classList.add("border-[#DC143C]", "bg-[#DC143C]/5");
        } else {
            gridRow.classList.remove("border-[#DC143C]", "bg-[#DC143C]/5");
            gridRow.classList.add("border-[#263245]", "hover:border-[#3D4F6B]");
        }

        // Tag wrapper
        const gridTagWrapper = document.getElementById(`card-tag-wrapper-${cid}`);
        if (gridTagWrapper) {
            if (card.tag) {
                gridTagWrapper.innerHTML = `<button onclick="filterByTag('${safeTag}')" 
                    class="bg-[#9333EA]/20 hover:bg-[#9333EA]/35 text-[#C084FC] hover:text-white border border-[#9333EA]/50 text-[10px] px-2 py-0.5 font-mono uppercase font-bold tracking-wider transition flex items-center space-x-1"
                    title="Filter by tag: ${card.tag}">
                    <span>🏷️ ${card.tag}</span>
                </button>`;
            } else {
                gridTagWrapper.innerHTML = `<button onclick="${editOnclickStr}"
                    class="bg-[#10141D] hover:bg-[#222B3D] text-[#64748B] hover:text-[#C084FC] border border-[#263245] hover:border-[#9333EA]/50 text-[10px] px-1.5 py-0.5 font-mono uppercase transition"
                    title="Assign Tag / Deck">
                    + Tag
                </button>`;
            }
        }

        // Target value
        const gridTargetVal = document.getElementById(`card-target-val-${cid}`);
        if (gridTargetVal) {
            gridTargetVal.innerHTML = card.target_price !== null && card.target_price !== undefined
                ? `$${card.target_price.toFixed(2)}`
                : `<span class="text-[#64748B] italic text-xs font-normal">None</span>`;
        }

        // Lowest live price color
        const gridLowestVal = document.getElementById(`card-lowest-val-${cid}`);
        if (gridLowestVal) {
            if (card.is_deal) {
                gridLowestVal.classList.add("text-[#FF3358]");
                gridLowestVal.classList.remove("text-[#00CED1]");
            } else {
                gridLowestVal.classList.add("text-[#00CED1]");
                gridLowestVal.classList.remove("text-[#FF3358]");
            }
        }

        // Deal banner
        const gridDealBanner = document.getElementById(`card-deal-banner-container-${cid}`);
        if (gridDealBanner) {
            if (card.is_deal) {
                const savingsTxt = card.savings_amount ? card.savings_amount.toFixed(2) : "0.00";
                const percentTxt = card.savings_percent !== undefined ? card.savings_percent : 0;
                gridDealBanner.innerHTML = `<div class="bg-[#DC143C]/20 border border-[#DC143C] text-[#FF3358] text-xs px-3 py-1.5 flex items-center justify-between font-mono font-bold">
                    <span>[!] TARGET ACQUIRED</span>
                    <span>-$${savingsTxt} (${percentTxt}% OFF)</span>
                </div>`;
            } else if (card.target_price && card.lowest_price) {
                const diff = (card.lowest_price - card.target_price).toFixed(2);
                gridDealBanner.innerHTML = `<div class="bg-[#10141D] text-[#94A3B8] text-xs px-2.5 py-1 border border-[#263245] text-right font-mono">
                    +$${diff} above threshold
                </div>`;
            } else {
                gridDealBanner.innerHTML = "";
            }
        }

        // Edit button onclick
        const gridEditBtn = document.getElementById(`card-edit-btn-${cid}`);
        if (gridEditBtn) gridEditBtn.setAttribute("onclick", editOnclickStr);
    }

    // 2. COMPACT / LIST VIEW UPDATES
    const compactCard = document.getElementById(`compact-card-${cid}`);
    if (compactCard) {
        compactCard.dataset.tag = (card.tag || "").toLowerCase().trim();
        compactCard.dataset.isDeal = card.is_deal ? "true" : "false";

        if (card.is_deal) {
            compactCard.classList.add("is-deal");
        } else {
            compactCard.classList.remove("is-deal");
        }

        // Deal badge
        const compactDealBadge = document.getElementById(`compact-deal-badge-${cid}`);
        if (compactDealBadge) {
            if (card.is_deal) {
                compactDealBadge.classList.remove("hidden");
            } else {
                compactDealBadge.classList.add("hidden");
            }
        }

        // Tag wrapper
        const compactTagWrapper = document.getElementById(`compact-tag-wrapper-${cid}`);
        if (compactTagWrapper) {
            if (card.tag) {
                compactTagWrapper.innerHTML = `<span class="text-[#C084FC] bg-[#9333EA]/20 border border-[#9333EA]/40 px-1.5 py-0.2 uppercase text-[10px]">🏷️ ${card.tag}</span>`;
            } else {
                compactTagWrapper.innerHTML = "";
            }
        }

        // Target value
        const compactTargetVal = document.getElementById(`compact-target-val-${cid}`);
        if (compactTargetVal) {
            compactTargetVal.textContent = card.target_price !== null && card.target_price !== undefined
                ? `$${card.target_price.toFixed(2)}`
                : "--";
        }

        // Lowest price color
        const compactLowestVal = document.getElementById(`compact-lowest-val-${cid}`);
        if (compactLowestVal) {
            if (card.is_deal) {
                compactLowestVal.classList.add("text-[#FF3358]");
                compactLowestVal.classList.remove("text-[#00CED1]");
            } else {
                compactLowestVal.classList.add("text-[#00CED1]");
                compactLowestVal.classList.remove("text-[#FF3358]");
            }
        }

        // Edit button
        const compactEditBtn = document.getElementById(`compact-edit-btn-${cid}`);
        if (compactEditBtn) compactEditBtn.setAttribute("onclick", editOnclickStr);
    }

    // 3. SWIPE VIEW UPDATES
    const swipeSlide = document.getElementById(`swipe-card-slide-${cid}`);
    if (swipeSlide) {
        swipeSlide.dataset.tag = (card.tag || "").toLowerCase().trim();
        swipeSlide.dataset.isDeal = card.is_deal ? "true" : "false";

        const swipeBox = document.getElementById(`swipe-card-box-${cid}`);
        if (swipeBox) {
            if (card.is_deal) {
                swipeBox.classList.remove("border-[#263245]");
                swipeBox.classList.add("border-[#DC143C]", "bg-[#DC143C]/5");
            } else {
                swipeBox.classList.remove("border-[#DC143C]", "bg-[#DC143C]/5");
                swipeBox.classList.add("border-[#263245]");
            }
        }

        const swipeTagWrapper = document.getElementById(`swipe-tag-wrapper-${cid}`);
        if (swipeTagWrapper) {
            if (card.tag) {
                swipeTagWrapper.innerHTML = `<span class="bg-[#9333EA]/20 text-[#C084FC] border border-[#9333EA]/50 text-xs px-2 py-0.5 font-mono uppercase font-bold">🏷️ ${card.tag}</span>`;
            } else {
                swipeTagWrapper.innerHTML = "";
            }
        }

        const swipeTargetVal = document.getElementById(`swipe-target-val-${cid}`);
        if (swipeTargetVal) {
            swipeTargetVal.textContent = card.target_price !== null && card.target_price !== undefined
                ? `$${card.target_price.toFixed(2)}`
                : "None";
        }

        const swipeLowestVal = document.getElementById(`swipe-lowest-val-${cid}`);
        if (swipeLowestVal) {
            if (card.is_deal) {
                swipeLowestVal.classList.add("text-[#FF3358]");
                swipeLowestVal.classList.remove("text-[#00CED1]");
            } else {
                swipeLowestVal.classList.add("text-[#00CED1]");
                swipeLowestVal.classList.remove("text-[#FF3358]");
            }
        }

        const swipeDealBanner = document.getElementById(`swipe-deal-banner-container-${cid}`);
        if (swipeDealBanner) {
            if (card.is_deal) {
                const savingsTxt = card.savings_amount ? card.savings_amount.toFixed(2) : "0.00";
                const percentTxt = card.savings_percent !== undefined ? card.savings_percent : 0;
                swipeDealBanner.innerHTML = `<div class="mt-3 bg-[#DC143C]/20 border border-[#DC143C] text-[#FF3358] text-xs px-3 py-2 flex items-center justify-between font-mono font-bold">
                    <span>[!] TARGET ACQUIRED</span>
                    <span>-$${savingsTxt} (${percentTxt}% OFF)</span>
                </div>`;
            } else {
                swipeDealBanner.innerHTML = "";
            }
        }

        const swipeEditBtn = document.getElementById(`swipe-edit-btn-${cid}`);
        if (swipeEditBtn) swipeEditBtn.setAttribute("onclick", editOnclickStr);
    }

    // 4. MM ALERT BUTTON & STATUS
    const mmStatus = document.getElementById(`mm-alert-status-${cid}`);
    if (mmStatus) {
        mmStatus.textContent = card.notify_mm_stock ? "ON" : "OFF";
    }
    const mmBtn = document.getElementById(`mm-alert-btn-${cid}`);
    if (mmBtn) {
        if (card.notify_mm_stock) {
            mmBtn.className = "px-1.5 py-0.5 text-[10px] font-mono uppercase font-bold border transition flex items-center space-x-1 bg-[#00CED1]/15 text-[#00CED1] border-[#00CED1]/50 hover:bg-[#00CED1]/25";
        } else {
            mmBtn.className = "px-1.5 py-0.5 text-[10px] font-mono uppercase font-bold border transition flex items-center space-x-1 bg-[#10141D] text-[#64748B] border-[#263245] hover:text-[#94A3B8]";
        }
    }

    // 5. UPDATE TELEMETRY KPIS
    updateRegistryKpis();

    // 6. UPDATE TAG PILLS & DROPDOWN
    refreshRegistryTagsDom();

    // 7. RE-APPLY ACTIVE CLIENT-SIDE FILTERS
    if (typeof filterWatchlist === "function") {
        filterWatchlist();
    }
}

/**
 * Dynamically recalculates telemetry KPIs (Active Deals and Target Portfolio)
 * from live cards currently present in the DOM.
 */
function updateRegistryKpis() {
    const allGridCards = document.querySelectorAll("#watchlist-grid .watchlist-card");
    let dealsCount = 0;
    let targetSum = 0;
    allGridCards.forEach(c => {
        if (c.dataset.isDeal === "true") dealsCount++;
        const cardId = c.id.replace("card-row-", "");
        const targetValSpan = document.getElementById(`card-target-val-${cardId}`);
        if (targetValSpan) {
            const txt = targetValSpan.textContent.replace("$", "").trim();
            const val = parseFloat(txt);
            if (!isNaN(val)) targetSum += val;
        }
    });

    const dealsCountElem = document.getElementById("kpi-deals-count");
    const dealsBoxElem = document.getElementById("kpi-deals-box");
    const dealsTitleElem = document.getElementById("kpi-deals-title");
    const dealsBadgeElem = document.getElementById("kpi-deals-badge");
    if (dealsCountElem) {
        dealsCountElem.textContent = dealsCount;
        if (dealsCount > 0) {
            dealsCountElem.classList.add("text-[#FF3358]");
            dealsCountElem.classList.remove("text-white");
            if (dealsBoxElem) {
                dealsBoxElem.classList.add("border-[#DC143C]", "bg-[#DC143C]/10");
                dealsBoxElem.classList.remove("border-[#263245]");
            }
            if (dealsTitleElem) {
                dealsTitleElem.classList.add("text-[#FF3358]");
                dealsTitleElem.classList.remove("text-[#94A3B8]");
            }
            if (dealsBadgeElem) {
                dealsBadgeElem.classList.add("text-[#FF3358]", "font-bold", "animate-tactical-pulse");
                dealsBadgeElem.classList.remove("text-[#94A3B8]");
            }
        } else {
            dealsCountElem.classList.remove("text-[#FF3358]");
            dealsCountElem.classList.add("text-white");
            if (dealsBoxElem) {
                dealsBoxElem.classList.remove("border-[#DC143C]", "bg-[#DC143C]/10");
                dealsBoxElem.classList.add("border-[#263245]");
            }
            if (dealsTitleElem) {
                dealsTitleElem.classList.remove("text-[#FF3358]");
                dealsTitleElem.classList.add("text-[#94A3B8]");
            }
            if (dealsBadgeElem) {
                dealsBadgeElem.classList.remove("text-[#FF3358]", "font-bold", "animate-tactical-pulse");
                dealsBadgeElem.classList.add("text-[#94A3B8]");
            }
        }
    }

    const targetPortfolioElem = document.getElementById("kpi-target-portfolio");
    if (targetPortfolioElem) {
        targetPortfolioElem.textContent = `$${targetSum.toFixed(2)}`;
    }
}

/**
 * Synchronizes tag filter dropdown, quick filter pills bar, and autocomplete datalist
 * based on all active tags present across loaded cards.
 */
function refreshRegistryTagsDom() {
    const tagDropdown = document.getElementById("watchlist-filter-tag");
    const pillsBar = document.getElementById("watchlist-tag-pills-bar");
    if (!tagDropdown && !pillsBar) return;

    const cards = document.querySelectorAll("#watchlist-grid .watchlist-card");
    const tagMap = new Map();
    const tagCounts = {};
    cards.forEach(c => {
        const rawTag = (c.dataset.tag || "").trim();
        if (rawTag) {
            const lower = rawTag.toLowerCase();
            tagCounts[lower] = (tagCounts[lower] || 0) + 1;
            if (!tagMap.has(lower)) {
                const badge = c.querySelector(`[id^="card-tag-wrapper-"] button span`);
                const badgeText = badge ? badge.textContent.replace("🏷️", "").trim() : rawTag;
                tagMap.set(lower, badgeText || rawTag);
            }
        }
    });

    const sortedTagKeys = Array.from(tagMap.keys()).sort();
    const currentDropdownVal = (tagDropdown ? tagDropdown.value : "all").toLowerCase();

    if (tagDropdown) {
        let opts = `<option value="all">ALL TAGS</option>`;
        sortedTagKeys.forEach(k => {
            const disp = tagMap.get(k);
            opts += `<option value="${k}" ${currentDropdownVal === k ? 'selected' : ''}>🏷️ ${disp}</option>`;
        });
        opts += `<option value="__untagged__" ${currentDropdownVal === '__untagged__' ? 'selected' : ''}>[ UNTAGGED ]</option>`;
        tagDropdown.innerHTML = opts;
    }

    if (pillsBar) {
        let pillsHtml = `<span class="text-[10px] font-mono text-[#64748B] uppercase font-bold mr-1 flex-shrink-0">TAGS:</span>`;
        pillsHtml += `<button onclick="filterByTag('all')" class="tag-pill tag-pill-btn ${(!currentDropdownVal || currentDropdownVal === 'all') ? 'active' : ''} flex-shrink-0" data-tag="all">ALL (${cards.length})</button>`;
        sortedTagKeys.forEach(k => {
            const disp = tagMap.get(k);
            const isActive = currentDropdownVal === k;
            pillsHtml += `<button onclick="filterByTag('${disp.replace(/'/g, "\\'")}')" class="tag-pill tag-pill-btn ${isActive ? 'active' : ''} flex-shrink-0" data-tag="${k}">🏷️ ${disp} (${tagCounts[k]})</button>`;
        });
        pillsHtml += `<button onclick="filterByTag('__untagged__')" class="tag-pill tag-pill-btn ${currentDropdownVal === '__untagged__' ? 'active' : ''} flex-shrink-0" data-tag="__untagged__">UNTAGGED</button>`;
        pillsBar.innerHTML = pillsHtml;
    }

    const datalist = document.getElementById("user-tags-datalist");
    if (datalist && sortedTagKeys.length > 0) {
        datalist.innerHTML = sortedTagKeys.map(k => `<option value="${tagMap.get(k)}">`).join("");
    }
}

// =========================================================================
// Toggle Mighty Meeple Stock Alert (Per Card)
// =========================================================================
async function toggleMightyMeepleAlert(itemId) {
    try {
        const res = await fetch(`/api/watchlist/toggle-mm-alert/${itemId}`, { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, "success");
            const btn = document.getElementById(`mm-alert-btn-${itemId}`);
            const statusSpan = document.getElementById(`mm-alert-status-${itemId}`);
            if (statusSpan) {
                statusSpan.textContent = data.notify_mm_stock ? "ON" : "OFF";
            }
            if (btn) {
                if (data.notify_mm_stock) {
                    btn.className = "px-1.5 py-0.5 text-[10px] font-mono uppercase font-bold border transition flex items-center space-x-1 bg-[#00CED1]/15 text-[#00CED1] border-[#00CED1]/50 hover:bg-[#00CED1]/25";
                } else {
                    btn.className = "px-1.5 py-0.5 text-[10px] font-mono uppercase font-bold border transition flex items-center space-x-1 bg-[#10141D] text-[#64748B] border-[#263245] hover:text-[#94A3B8]";
                }
            }
        } else {
            showToast(data.error || "Failed to toggle Mighty Meeple alert", "error");
        }
    } catch (err) {
        console.error("Error toggling MM alert:", err);
        showToast("Communication error toggling alert", "error");
    }
}

// =========================================================================
// Refresh Single Card & Refresh All
// =========================================================================
async function triggerRefreshCard(itemId) {
    const icon = document.querySelector(`.refresh-icon-${itemId}`);
    if (icon) icon.classList.add("animate-spin-custom");

    try {
        const res = await fetch(`/api/watchlist/refresh/${itemId}`, { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Target price intel refreshed", "success");
            setTimeout(() => window.location.reload(), 450);
        } else {
            showToast(data.error || "Failed to refresh target intel", "error");
        }
    } catch (err) {
        console.error("Refresh error:", err);
        showToast("Failed to refresh card telemetry", "error");
    } finally {
        if (icon) icon.classList.remove("animate-spin-custom");
    }
}

async function triggerRefreshAll() {
    const icon = document.getElementById("refresh-all-icon");
    const btn = document.getElementById("btn-refresh-all");
    if (icon) icon.classList.add("animate-spin-custom");
    if (btn) btn.disabled = true;

    showToast("Executing price surveillance scan...", "info");

    try {
        const res = await fetch("/api/watchlist/refresh-all", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Telemetry synchronized", "success");
            setTimeout(() => window.location.reload(), 600);
        } else {
            showToast(data.error || "Price poll failed", "error");
        }
    } catch (err) {
        console.error("Refresh all error:", err);
        showToast("Surveillance poll failure", "error");
    } finally {
        if (icon) icon.classList.remove("animate-spin-custom");
        if (btn) btn.disabled = false;
    }
}

// =========================================================================
// Delete Card Action
// =========================================================================
async function deleteCard(itemId, cardName) {
    if (!confirm(`CONFIRM TARGET DE-REGISTRATION:\nRemove "${cardName}" from surveillance registry?`)) {
        return;
    }

    try {
        const res = await fetch(`/api/watchlist/delete/${itemId}`, { method: "DELETE" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Target ${cardName} de-registered`, "success");
            const elem = document.getElementById(`card-row-${itemId}`);
            if (elem) {
                elem.style.transition = "opacity 0.25s, transform 0.25s";
                elem.style.opacity = "0";
                elem.style.transform = "scale(0.96)";
                setTimeout(() => window.location.reload(), 300);
            } else {
                window.location.reload();
            }
        } else {
            showToast(data.error || "Failed to remove target", "error");
        }
    } catch (err) {
        console.error("Delete error:", err);
        showToast("Error removing target", "error");
    }
}

// =========================================================================
// Multi-Card Target Selection & Bulk Operations Engine
// =========================================================================
let selectedCardIds = new Set();
let bulkSelectModeActive = false;

function toggleBulkSelectMode() {
    bulkSelectModeActive = !bulkSelectModeActive;
    const btn = document.getElementById("btn-toggle-select-mode");
    const label = document.getElementById("btn-select-mode-text");
    if (btn) {
        if (bulkSelectModeActive) {
            btn.classList.add("active");
            if (label) label.textContent = "Exit Select";
        } else {
            btn.classList.remove("active");
            if (label) label.textContent = "Select";
            clearCardSelection();
        }
    }
    updateBulkActionBar();
}

function handleCardSelectChange(cardId, isChecked) {
    cardId = Number(cardId);
    if (isChecked) {
        selectedCardIds.add(cardId);
    } else {
        selectedCardIds.delete(cardId);
    }
    syncCardSelectionUI(cardId);
    updateBulkActionBar();
}

function toggleSingleCardSelection(cardId) {
    cardId = Number(cardId);
    const isSelected = selectedCardIds.has(cardId);
    handleCardSelectChange(cardId, !isSelected);
}

function syncCardSelectionUI(cardId) {
    const isSelected = selectedCardIds.has(cardId);

    // Synchronize checkboxes
    const chkGrid = document.getElementById(`chk-grid-${cardId}`);
    if (chkGrid) chkGrid.checked = isSelected;
    const chkCompact = document.getElementById(`chk-compact-${cardId}`);
    if (chkCompact) chkCompact.checked = isSelected;

    // Synchronize button labels & styling across Grid, Compact, Swipe
    const labels = document.querySelectorAll(`.btn-select-label-${cardId}`);
    labels.forEach(lbl => {
        lbl.textContent = isSelected ? "✓ Selected" : "Select";
        const btn = lbl.closest("button");
        if (btn) {
            if (isSelected) {
                btn.classList.add("text-[#00CED1]");
                btn.classList.remove("text-[#94A3B8]");
            } else {
                btn.classList.remove("text-[#00CED1]");
                btn.classList.add("text-[#94A3B8]");
            }
        }
    });

    // Synchronize visual card container highlights
    const gridCard = document.getElementById(`card-row-${cardId}`);
    if (gridCard) {
        if (isSelected) gridCard.classList.add("is-selected");
        else gridCard.classList.remove("is-selected");
    }

    const compactCard = document.getElementById(`compact-card-${cardId}`);
    if (compactCard) {
        if (isSelected) compactCard.classList.add("is-selected");
        else compactCard.classList.remove("is-selected");
    }
}

function getCurrentlyVisibleCardIds() {
    const gridCards = document.querySelectorAll("#watchlist-grid .watchlist-card");
    const visibleIds = [];
    if (gridCards.length > 0) {
        gridCards.forEach(card => {
            if (!card.classList.contains("hidden")) {
                const id = Number(card.id.replace("card-row-", ""));
                if (id) visibleIds.push(id);
            }
        });
    } else {
        const compactCards = document.querySelectorAll("#watchlist-compact .compact-card");
        compactCards.forEach(card => {
            if (!card.classList.contains("hidden")) {
                const id = Number(card.id.replace("compact-card-", ""));
                if (id) visibleIds.push(id);
            }
        });
    }
    return visibleIds;
}

function updateBulkActionBar() {
    const bar = document.getElementById("watchlist-bulk-bar");
    const badge = document.getElementById("bulk-selected-count-badge");
    const count = selectedCardIds.size;

    if (badge) {
        badge.textContent = `[ ${count} TARGET${count === 1 ? '' : 'S'} SELECTED ]`;
    }

    if (bar) {
        if (count > 0) {
            bar.classList.remove("hidden");
        } else {
            bar.classList.add("hidden");
        }
    }

    const selectAllBtn = document.getElementById("btn-bulk-select-all");
    if (selectAllBtn) {
        const visibleCards = getCurrentlyVisibleCardIds();
        const allVisibleSelected = visibleCards.length > 0 && visibleCards.every(id => selectedCardIds.has(id));
        selectAllBtn.textContent = allVisibleSelected ? "Deselect All" : "Select All";
    }
}

function selectAllVisibleCards() {
    const visibleIds = getCurrentlyVisibleCardIds();
    const allVisibleSelected = visibleIds.length > 0 && visibleIds.every(id => selectedCardIds.has(id));

    if (allVisibleSelected) {
        visibleIds.forEach(id => {
            selectedCardIds.delete(id);
            syncCardSelectionUI(id);
        });
    } else {
        visibleIds.forEach(id => {
            selectedCardIds.add(id);
            syncCardSelectionUI(id);
        });
    }
    updateBulkActionBar();
}

function clearCardSelection() {
    const prevSelected = Array.from(selectedCardIds);
    selectedCardIds.clear();
    prevSelected.forEach(id => syncCardSelectionUI(id));
    updateBulkActionBar();
}

async function executeBulkTerminate() {
    const count = selectedCardIds.size;
    if (count === 0) {
        showToast("No targets selected.", "error");
        return;
    }

    if (!confirm(`CONFIRM BULK TARGET DE-REGISTRATION:\nPermanently remove ${count} target(s) from surveillance registry?`)) {
        return;
    }

    const cardIds = Array.from(selectedCardIds);
    try {
        const res = await fetch("/api/watchlist/bulk-delete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ card_ids: cardIds }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `De-registered ${count} targets.`, "success");
            cardIds.forEach(id => {
                const elem = document.getElementById(`card-row-${id}`);
                const compactElem = document.getElementById(`compact-card-${id}`);
                [elem, compactElem].forEach(el => {
                    if (el) {
                        el.style.transition = "opacity 0.25s, transform 0.25s";
                        el.style.opacity = "0";
                        el.style.transform = "scale(0.96)";
                    }
                });
            });
            clearCardSelection();
            setTimeout(() => window.location.reload(), 350);
        } else {
            showToast(data.error || "Failed to remove targets.", "error");
        }
    } catch (err) {
        console.error("Bulk delete error:", err);
        showToast("Network error removing targets.", "error");
    }
}

function openBulkTagModal() {
    const count = selectedCardIds.size;
    if (count === 0) {
        showToast("No targets selected for tagging.", "error");
        return;
    }
    const modal = document.getElementById("modal-bulk-tag");
    const countLabel = document.getElementById("bulk-tag-count-label");
    const input = document.getElementById("bulk-assign-tag-input");

    if (countLabel) {
        countLabel.textContent = `${count} Target${count === 1 ? '' : 's'} Selected`;
    }
    if (input) {
        input.value = "";
        input.placeholder = "e.g. Atraxa Commander, Modern Burn, Sideboard...";
    }
    if (modal) {
        modal.classList.remove("hidden");
        if (input) input.focus();
    }
}

function closeBulkTagModal() {
    const modal = document.getElementById("modal-bulk-tag");
    if (modal) modal.classList.add("hidden");
}

function clearBulkTagInput() {
    const input = document.getElementById("bulk-assign-tag-input");
    if (input) {
        input.value = "";
        input.placeholder = "[ TAGS WILL BE CLEARED / STRIPPED ]";
        input.focus();
    }
}

async function submitBulkTag() {
    const count = selectedCardIds.size;
    if (count === 0) {
        showToast("No targets selected.", "error");
        return;
    }
    const input = document.getElementById("bulk-assign-tag-input");
    const tag = input ? input.value.trim() : "";
    const cardIds = Array.from(selectedCardIds);

    const submitBtn = document.getElementById("btn-submit-bulk-tag");
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Applying...";
    }

    try {
        const res = await fetch("/api/watchlist/bulk-tag", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ card_ids: cardIds, tag: tag }),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || `Updated tags for ${count} targets.`, "success");
            closeBulkTagModal();
            clearCardSelection();
            setTimeout(() => window.location.reload(), 350);
        } else {
            showToast(data.error || "Failed to update tags.", "error");
            if (submitBtn) {
                submitBtn.disabled = false;
                submitBtn.textContent = "Apply Tags";
            }
        }
    } catch (err) {
        console.error("Bulk tag error:", err);
        showToast("Network error updating tags.", "error");
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = "Apply Tags";
        }
    }
}

// =========================================================================
// View Mode Switching & Swipe Deck Touch Engine
// =========================================================================
function setViewMode(mode) {
    activeViewMode = mode;
    localStorage.setItem("chimaera_view_mode", mode);

    // Update buttons
    const btns = {
        grid: document.getElementById("btn-view-grid"),
        compact: document.getElementById("btn-view-compact"),
        swipe: document.getElementById("btn-view-swipe")
    };
    Object.keys(btns).forEach(k => {
        if (btns[k]) {
            if (k === mode) btns[k].classList.add("active");
            else btns[k].classList.remove("active");
        }
    });

    // Update Registry Containers if present
    const rGrid = document.getElementById("watchlist-grid");
    const rCompact = document.getElementById("watchlist-compact");
    const rSwipe = document.getElementById("watchlist-swipe");

    if (rGrid || rCompact || rSwipe) {
        if (rGrid) rGrid.classList.add("hidden");
        if (rCompact) rCompact.classList.add("hidden");
        if (rSwipe) rSwipe.classList.add("hidden");

        if (mode === "compact" && rCompact) {
            rCompact.classList.remove("hidden");
        } else if (mode === "swipe" && rSwipe) {
            rSwipe.classList.remove("hidden");
            updateSwipeDeckPosition("registry-swipe-track");
        } else if (rGrid) {
            rGrid.classList.remove("hidden");
        }
    }

    // Update Deals Containers if present
    const dGrid = document.getElementById("deals-grid");
    const dCompact = document.getElementById("deals-compact");
    const dSwipe = document.getElementById("deals-swipe");

    if (dGrid || dCompact || dSwipe) {
        if (dGrid) dGrid.classList.add("hidden");
        if (dCompact) dCompact.classList.add("hidden");
        if (dSwipe) dSwipe.classList.add("hidden");

        if (mode === "compact" && dCompact) {
            dCompact.classList.remove("hidden");
        } else if (mode === "swipe" && dSwipe) {
            dSwipe.classList.remove("hidden");
            updateSwipeDeckPosition("deals-swipe-track");
        } else if (dGrid) {
            dGrid.classList.remove("hidden");
        }
    }
}

function initSwipeDeck(trackId, wrapperId) {
    const track = document.getElementById(trackId);
    const wrapper = document.getElementById(wrapperId);
    if (!track || !wrapper) return;

    let startX = 0;
    let startY = 0;
    let isSwiping = false;

    wrapper.addEventListener("touchstart", (e) => {
        if (e.touches.length === 1) {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            isSwiping = true;
        }
    }, { passive: true });

    wrapper.addEventListener("touchend", (e) => {
        if (!isSwiping) return;
        isSwiping = false;
        const endX = e.changedTouches[0].clientX;
        const endY = e.changedTouches[0].clientY;
        const diffX = endX - startX;
        const diffY = endY - startY;

        // Horizontal swipe detected if horizontal distance exceeds vertical and is > 35px
        if (Math.abs(diffX) > Math.abs(diffY) && Math.abs(diffX) > 35) {
            if (diffX < 0) {
                swipeDeckNext(trackId);
            } else {
                swipeDeckPrev(trackId);
            }
        }
    }, { passive: true });
}

function getVisibleSlides(trackId) {
    const track = document.getElementById(trackId);
    if (!track) return [];
    const slides = Array.from(track.querySelectorAll(".swipe-card-slide"));
    return slides.filter(slide => !slide.classList.contains("hidden"));
}

function swipeDeckNext(trackId) {
    const visibleSlides = getVisibleSlides(trackId);
    if (visibleSlides.length === 0) return;

    if (!swipeTrackState[trackId]) swipeTrackState[trackId] = { index: 0 };
    let curr = swipeTrackState[trackId].index;
    if (curr < visibleSlides.length - 1) {
        swipeTrackState[trackId].index = curr + 1;
    } else {
        swipeTrackState[trackId].index = 0;
    }
    updateSwipeDeckPosition(trackId);
}

function swipeDeckPrev(trackId) {
    const visibleSlides = getVisibleSlides(trackId);
    if (visibleSlides.length === 0) return;

    if (!swipeTrackState[trackId]) swipeTrackState[trackId] = { index: 0 };
    let curr = swipeTrackState[trackId].index;
    if (curr > 0) {
        swipeTrackState[trackId].index = curr - 1;
    } else {
        swipeTrackState[trackId].index = visibleSlides.length - 1;
    }
    updateSwipeDeckPosition(trackId);
}

function updateSwipeDeckPosition(trackId) {
    const track = document.getElementById(trackId);
    if (!track) return;
    const visibleSlides = getVisibleSlides(trackId);
    const indicatorId = trackId === "registry-swipe-track" ? "registry-swipe-indicator" : "deals-swipe-indicator";
    const indicator = document.getElementById(indicatorId);

    if (visibleSlides.length === 0) {
        if (indicator) indicator.textContent = "00 / 00";
        return;
    }

    if (!swipeTrackState[trackId]) swipeTrackState[trackId] = { index: 0 };
    if (swipeTrackState[trackId].index >= visibleSlides.length) {
        swipeTrackState[trackId].index = 0;
    }

    const activeIndex = swipeTrackState[trackId].index;
    const activeSlide = visibleSlides[activeIndex];

    const allSlides = Array.from(track.querySelectorAll(".swipe-card-slide"));
    const realIndex = allSlides.indexOf(activeSlide);

    track.style.transform = `translateX(-${realIndex * 100}%)`;

    if (indicator) {
        const curStr = String(activeIndex + 1).padStart(2, "0");
        const totStr = String(visibleSlides.length).padStart(2, "0");
        indicator.textContent = `${curStr} / ${totStr}`;
    }
}

// =========================================================================
// Filter & Search Watchlist Client-side (Grid, Compact, Swipe Deck)
// =========================================================================
function filterWatchlist() {
    const searchVal = (document.getElementById("watchlist-search")?.value || "").toLowerCase().trim();
    const dealFilter = document.getElementById("watchlist-filter-deal")?.value || "all";
    const tagFilter = (document.getElementById("watchlist-filter-tag")?.value || "all").toLowerCase().trim();
    const cards = document.querySelectorAll(".watchlist-card");

    let visibleGridCount = 0;

    cards.forEach(card => {
        const name = card.dataset.name || "";
        const set = card.dataset.set || "";
        const tag = (card.dataset.tag || "").toLowerCase().trim();
        const isDeal = card.dataset.isDeal === "true";
        const inStock = card.dataset.inStock === "true";
        const mmStock = card.dataset.mmStock === "true";
        const isAny = card.dataset.isAny === "true";

        const matchesSearch = !searchVal || name.includes(searchVal) || set.includes(searchVal) || tag.includes(searchVal);
        let matchesDeal = true;

        if (dealFilter === "deals") {
            matchesDeal = isDeal;
        } else if (dealFilter === "in_stock") {
            matchesDeal = inStock;
        } else if (dealFilter === "mm_in_stock") {
            matchesDeal = mmStock;
        } else if (dealFilter === "any_version") {
            matchesDeal = isAny;
        } else if (dealFilter === "specific_print") {
            matchesDeal = !isAny;
        }

        let matchesTag = true;
        if (tagFilter === "__untagged__") {
            matchesTag = !tag;
        } else if (tagFilter !== "all" && tagFilter !== "") {
            matchesTag = (tag === tagFilter);
        }

        if (matchesSearch && matchesDeal && matchesTag) {
            card.classList.remove("hidden");
            if (card.closest("#watchlist-grid")) visibleGridCount++;
        } else {
            card.classList.add("hidden");
        }
    });

    const headerCounter = document.getElementById("header-assets-counter");
    if (headerCounter) {
        headerCounter.textContent = `[ ${visibleGridCount} ASSETS ]`;
    }

    if (swipeTrackState["registry-swipe-track"]) {
        swipeTrackState["registry-swipe-track"].index = 0;
    }
    updateSwipeDeckPosition("registry-swipe-track");
    updateBulkActionBar();
}

// =========================================================================
// Filter & Search Deals Client-side (Grid, Compact, Swipe Deck)
// =========================================================================
function filterDeals() {
    const searchVal = (document.getElementById("deals-search")?.value || "").toLowerCase().trim();
    const tagFilter = (document.getElementById("deals-filter-tag")?.value || "all").toLowerCase().trim();
    const dealCards = document.querySelectorAll(".deal-card");

    let visibleDealsCount = 0;

    dealCards.forEach(card => {
        const name = card.dataset.name || "";
        const set = card.dataset.set || "";
        const tag = (card.dataset.tag || "").toLowerCase().trim();

        const matchesSearch = !searchVal || name.includes(searchVal) || set.includes(searchVal) || tag.includes(searchVal);
        let matchesTag = true;
        if (tagFilter === "__untagged__") {
            matchesTag = !tag;
        } else if (tagFilter !== "all" && tagFilter !== "") {
            matchesTag = (tag === tagFilter);
        }

        if (matchesSearch && matchesTag) {
            card.classList.remove("hidden");
            if (card.closest("#deals-grid")) visibleDealsCount++;
        } else {
            card.classList.add("hidden");
        }
    });

    const headerDealsCounter = document.getElementById("header-deals-counter");
    if (headerDealsCounter) {
        headerDealsCounter.textContent = `[ ${visibleDealsCount} DEALS ]`;
    }

    if (swipeTrackState["deals-swipe-track"]) {
        swipeTrackState["deals-swipe-track"].index = 0;
    }
    updateSwipeDeckPosition("deals-swipe-track");
}

// =========================================================================
// Discord Webhook Test Action
// =========================================================================
async function testDiscordWebhook() {
    showToast("Transmitting Discord telemetry ping...", "info");
    try {
        const res = await fetch("/api/discord/test", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message, "success");
        } else {
            showToast(data.error || "Transmission failed", "error");
        }
    } catch (err) {
        showToast("Transmission error to Discord endpoint", "error");
    }
}

// =========================================================================
// Cadence & Telemetry Settings Modal Controls
// =========================================================================
async function openCadenceModal() {
    const modal = document.getElementById("modal-cadence-settings");
    if (!modal) return;
    modal.classList.remove("hidden");
    await fetchCadenceTelemetry();
}

function closeCadenceModal() {
    const modal = document.getElementById("modal-cadence-settings");
    if (modal) modal.classList.add("hidden");
}

async function fetchCadenceTelemetry() {
    try {
        const res = await fetch("/api/settings/telemetry");
        const data = await res.json();

        // Update nav badge/label if exists
        const navLabel = document.getElementById("nav-cadence-label");
        if (navLabel) {
            const hours = data.poll_interval_hours;
            const statusTxt = data.auto_poll_enabled ? `${hours}h` : "PAUSED";
            navLabel.textContent = `Cadence: ${statusTxt}`;
        }

        // Update modal fields
        const select = document.getElementById("cadence-interval-select");
        const autoCheck = document.getElementById("cadence-auto-enabled");
        const mmCheck = document.getElementById("cadence-notify-mm");
        const ebaySelect = document.getElementById("cadence-ebay-mode");
        const workerStatusElem = document.getElementById("telemetry-worker-status");
        const lastPollElem = document.getElementById("telemetry-last-poll");
        const statusMsgElem = document.getElementById("telemetry-status-message");

        if (select) {
            const valStr = String(data.poll_interval_hours);
            let found = false;
            Array.from(select.options).forEach(opt => {
                if (parseFloat(opt.value) === parseFloat(valStr)) {
                    opt.selected = true;
                    found = true;
                }
            });
            if (!found) {
                const opt = document.createElement("option");
                opt.value = valStr;
                opt.textContent = `Custom (${valStr}h)`;
                opt.selected = true;
                select.appendChild(opt);
            }
        }

        if (autoCheck) {
            autoCheck.checked = Boolean(data.auto_poll_enabled);
        }

        if (mmCheck) {
            mmCheck.checked = Boolean(data.notify_mm_stock_enabled !== undefined ? data.notify_mm_stock_enabled : true);
        }

        if (ebaySelect && data.ebay_link_mode) {
            ebaySelect.value = data.ebay_link_mode;
        }

        if (workerStatusElem) {
            const st = (data.worker_status || "standby").toUpperCase();
            workerStatusElem.textContent = `[ ${st} ]`;
            if (st === "RUNNING" || st === "ONLINE") {
                workerStatusElem.className = "font-bold text-[#00CED1] uppercase";
            } else if (st === "ERROR") {
                workerStatusElem.className = "font-bold text-[#FF3358] uppercase";
            } else {
                workerStatusElem.className = "font-bold text-[#94A3B8] uppercase";
            }
        }

        if (lastPollElem) {
            if (data.last_poll_time) {
                lastPollElem.textContent = formatESTDate(data.last_poll_time);
            } else {
                lastPollElem.textContent = "Never";
            }
        }

        // Render modal last 3 successful sweeps
        const modalPollsList = document.getElementById("telemetry-recent-polls-list");
        if (modalPollsList) {
            const recent = data.recent_poll_times || (data.last_poll_time ? [data.last_poll_time] : []);
            if (recent.length > 0) {
                modalPollsList.innerHTML = recent.slice(0, 3).map((t, idx) => {
                    const isFirst = idx === 0;
                    const textClass = isFirst ? "text-white font-bold" : "text-[#94A3B8]";
                    const badgeClass = isFirst ? "text-[#00CED1] font-bold" : "text-[#64748B]";
                    return `<div class="flex items-center justify-between text-[11px] bg-[#10141D] px-2 py-1 border border-[#263245]">
                        <span class="${badgeClass}">#${idx + 1}</span>
                        <span class="${textClass}">${formatESTDate(t)}</span>
                    </div>`;
                }).join("");
            } else {
                modalPollsList.innerHTML = `<span class="text-[#64748B] text-[11px]">Awaiting initial sync</span>`;
            }
        }

        // Render index.html registry-last-scans-container if present
        const regScansContainer = document.getElementById("registry-last-scans-container");
        if (regScansContainer) {
            const recent = data.recent_poll_times || (data.last_poll_time ? [data.last_poll_time] : []);
            if (recent.length > 0) {
                regScansContainer.innerHTML = recent.slice(0, 3).map((t, idx) => {
                    const isFirst = idx === 0;
                    const borderClass = isFirst ? "border-[#00CED1]/50 text-white font-bold" : "border-[#263245] text-[#94A3B8]";
                    const badgeClass = isFirst ? "text-[#00CED1]" : "text-[#64748B]";
                    return `<span class="inline-flex items-center space-x-1 bg-[#1B2230] border ${borderClass} px-2 py-0.5 text-[10px]">
                        <span class="${badgeClass} font-bold">#${idx + 1}:</span>
                        <span>${formatESTDate(t)}</span>
                    </span>`;
                }).join("");
            } else {
                regScansContainer.innerHTML = `<span class="text-[#94A3B8] italic">Awaiting initial sync</span>`;
            }
        }

        const webhookInput = document.getElementById("user-discord-webhook-input");
        const routingBadge = document.getElementById("webhook-routing-badge");

        if (webhookInput) {
            webhookInput.value = data.user_discord_webhook_url || "";
        }
        if (routingBadge) {
            if (data.user_discord_webhook_url) {
                routingBadge.textContent = "Personal Channel";
                routingBadge.className = "text-[9px] font-mono px-1.5 py-0.2 bg-[#00CED1]/20 text-[#00CED1] border border-[#00CED1]/40 uppercase font-bold";
            } else if (data.global_discord_webhook_set) {
                routingBadge.textContent = "Default / Fallback";
                routingBadge.className = "text-[9px] font-mono px-1.5 py-0.2 bg-[#263245] text-[#94A3B8] uppercase font-bold";
            } else {
                routingBadge.textContent = "Unconfigured";
                routingBadge.className = "text-[9px] font-mono px-1.5 py-0.2 bg-[#DC143C]/20 text-[#FF3358] uppercase font-bold";
            }
        }

        if (statusMsgElem) {
            statusMsgElem.textContent = data.last_poll_status || "Surveillance nominal.";
        }

    } catch (err) {
        console.error("Error loading telemetry:", err);
    }
}

async function testUserDiscordWebhook() {
    const input = document.getElementById("user-discord-webhook-input");
    const btn = document.getElementById("btn-test-user-webhook");
    const url = input ? input.value.trim() : "";

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span>⏳ Verifying...</span>`;
    }

    try {
        const payload = url ? { webhook_url: url } : {};
        const res = await fetch("/api/discord/test", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Test webhook delivered successfully!", "success");
        } else {
            showToast(data.error || "Discord webhook verification failed.", "error");
        }
    } catch (err) {
        console.error("Test webhook error:", err);
        showToast("Connection to server failed during webhook test.", "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = `<span>🔔 Test Webhook</span>`;
        }
    }
}

async function saveCadenceSettings() {
    const select = document.getElementById("cadence-interval-select");
    const autoCheck = document.getElementById("cadence-auto-enabled");
    const mmCheck = document.getElementById("cadence-notify-mm");
    const ebaySelect = document.getElementById("cadence-ebay-mode");
    const webhookInput = document.getElementById("user-discord-webhook-input");
    const btn = document.getElementById("btn-save-cadence");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Committing...";
    }

    try {
        if (webhookInput) {
            const webhookUrl = webhookInput.value.trim();
            const whRes = await fetch("/api/user/settings/webhook", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ discord_webhook_url: webhookUrl }),
            });
            if (!whRes.ok) {
                const whData = await whRes.json();
                showToast(whData.error || "Invalid Discord webhook format", "error");
                return;
            }
        }

        const payload = {
            poll_interval_hours: select ? parseFloat(select.value) : 6.0,
            auto_poll_enabled: autoCheck ? autoCheck.checked : true,
            notify_mm_stock_enabled: mmCheck ? mmCheck.checked : true,
            ebay_link_mode: ebaySelect ? ebaySelect.value : "direct",
        };

        const res = await fetch("/api/settings/cadence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Surveillance & notification settings committed", "success");
            closeCadenceModal();
            await fetchCadenceTelemetry();
        } else {
            showToast(data.error || "Failed to update settings", "error");
        }
    } catch (err) {
        console.error("Save settings error:", err);
        showToast("Error updating settings", "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Save Settings";
        }
    }
}

async function triggerManualSweepFromModal() {
    closeCadenceModal();
    await triggerRefreshAll();
}

// =========================================================================
// Global Initialization & Keyboard Navigation
// =========================================================================
document.addEventListener("DOMContentLoaded", () => {
    fetchCadenceTelemetry();

    // Initialize Swipe Decks for touch gestures
    initSwipeDeck("registry-swipe-track", "registry-swipe-wrapper");
    initSwipeDeck("deals-swipe-track", "deals-swipe-wrapper");

    // Restore saved view mode preference
    const savedMode = localStorage.getItem("chimaera_view_mode") || "grid";
    setViewMode(savedMode);

    // Close Logo Dropdown Menu on click outside
    document.addEventListener("click", (e) => {
        const menu = document.getElementById("logo-dropdown-menu");
        const btn = document.getElementById("btn-logo-menu");
        if (menu && btn && !menu.classList.contains("hidden")) {
            if (!menu.contains(e.target) && !btn.contains(e.target)) {
                menu.classList.add("hidden");
                const chevron = document.getElementById("logo-menu-chevron");
                if (chevron) chevron.style.transform = "rotate(0deg)";
            }
        }
    });
});

// =========================================================================
// Logo Dropdown Tactical Menu Controls
// =========================================================================
function toggleLogoMenu() {
    const menu = document.getElementById("logo-dropdown-menu");
    const chevron = document.getElementById("logo-menu-chevron");
    if (!menu) return;
    menu.classList.toggle("hidden");
    if (chevron) {
        if (menu.classList.contains("hidden")) {
            chevron.style.transform = "rotate(0deg)";
        } else {
            chevron.style.transform = "rotate(180deg)";
        }
    }
}

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
                    <img src="${item.image_url}" alt="" class="w-9 h-12 object-cover border border-[#263245] bg-[#0C0F17] flex-shrink-0" onerror="this.style.display='none'">
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
                        <button onclick="updateBuylistCartQty(${idx}, -1)" class="px-2 py-0.5 text-xs text-[#94A3B8] hover:text-white hover:bg-[#263245]">-</button>
                        <span class="px-2 text-xs font-bold text-white">${item.qty}</span>
                        <button onclick="updateBuylistCartQty(${idx}, 1)" class="px-2 py-0.5 text-xs text-[#94A3B8] hover:text-white hover:bg-[#263245]">+</button>
                    </div>

                    <!-- Price Subtotal -->
                    <div class="text-right min-w-[70px]">
                        <div class="font-bold text-xs text-[#2DD4BF]">$${itemCreditSubtotal}</div>
                        <div class="text-[10px] text-[#94A3B8]">$${itemCashSubtotal} Cash</div>
                    </div>

                    <!-- Remove Item -->
                    <button onclick="removeFromBuylistCart(${idx})" class="text-[#64748B] hover:text-[#FF3358] text-base font-bold px-1" title="Remove item">&times;</button>
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
                    <img src="${cardImg}" alt="${card.card_name}" 
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

        const data = await res.json();
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
                        <img src="${q.image_url}" alt="" class="w-8 h-10 object-cover border border-[#263245] bg-[#0C0F17] flex-shrink-0" onerror="this.style.display='none'">
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
}

function closeBuylistVariantModal() {
    const modal = document.getElementById("modal-buylist-variants");
    if (modal) modal.classList.add("hidden");
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




