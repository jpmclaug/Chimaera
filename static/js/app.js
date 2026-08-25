// =========================================================================
// CHIMAERA // TACTICAL INTELLIGENCE CLIENT TELEMETRY & CONTROLS
// =========================================================================

let currentPrintsData = [];
let autocompleteTimeout = null;
let currentPriceIntel = { market: null, great: null, good: null, fair: null };
let editPriceIntel = { market: null, great: null, good: null, fair: null };

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
    
    toast.className = `bg-[#1B2230] ${borderColor} border text-[#F1F5F9] text-xs px-4 py-3 font-mono flex items-center space-x-2.5 transition-all duration-200 transform translate-y-2 opacity-0 pointer-events-auto shadow-xl`;
    toast.innerHTML = `
        <span class="${textColor} font-bold text-xs uppercase tracking-wider">${prefixTag}</span>
        <span class="tracking-tight text-xs text-[#F1F5F9]">${message}</span>
    `;

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
    });

    // Auto remove after 3.8 seconds
    setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 250);
    }, 3800);
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
    if (filter) {
        filter.value = (tagName || "").toLowerCase().trim();
        filterWatchlist();
    }
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
            setTimeout(() => window.location.reload(), 500);
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
// Edit Target Price & Alert Settings Submit Action
// =========================================================================
async function submitEditTarget() {
    const itemId = document.getElementById("edit-target-item-id").value;
    const targetPriceVal = document.getElementById("edit-target-price-input").value;
    const mmAlertChecked = document.getElementById("edit-target-mm-alert")?.checked ?? true;
    const tagInput = document.getElementById("edit-target-tag-input");
    const tagVal = (tagInput ? tagInput.value : "").trim();

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
            setTimeout(() => window.location.reload(), 450);
        } else {
            showToast(data.error || "Failed to reconfigure threshold", "error");
        }
    } catch (err) {
        console.error("Update target error:", err);
        showToast("Communication error updating target", "error");
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
// Filter & Search Watchlist Client-side
// =========================================================================
function filterWatchlist() {
    const searchVal = (document.getElementById("watchlist-search")?.value || "").toLowerCase().trim();
    const dealFilter = document.getElementById("watchlist-filter-deal")?.value || "all";
    const tagFilter = (document.getElementById("watchlist-filter-tag")?.value || "all").toLowerCase().trim();
    const cards = document.querySelectorAll(".watchlist-card");

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
        } else if (tagFilter !== "all") {
            matchesTag = (tag === tagFilter);
        }

        if (matchesSearch && matchesDeal && matchesTag) {
            card.classList.remove("hidden");
        } else {
            card.classList.add("hidden");
        }
    });
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
                try {
                    const dt = new Date(data.last_poll_time);
                    lastPollElem.textContent = dt.toLocaleString();
                } catch {
                    lastPollElem.textContent = data.last_poll_time;
                }
            } else {
                lastPollElem.textContent = "Never";
            }
        }

        if (statusMsgElem) {
            statusMsgElem.textContent = data.last_poll_status || "Surveillance nominal.";
        }

    } catch (err) {
        console.error("Error loading telemetry:", err);
    }
}

async function saveCadenceSettings() {
    const select = document.getElementById("cadence-interval-select");
    const autoCheck = document.getElementById("cadence-auto-enabled");
    const mmCheck = document.getElementById("cadence-notify-mm");
    const ebaySelect = document.getElementById("cadence-ebay-mode");
    const btn = document.getElementById("btn-save-cadence");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Committing...";
    }

    try {
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
            showToast(data.message || "Surveillance settings committed", "success");
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

// Fetch telemetry status on initial page load
document.addEventListener("DOMContentLoaded", () => {
    fetchCadenceTelemetry();
});

