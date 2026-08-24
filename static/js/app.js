// =========================================================================
// CHIMAERA // IMPERIAL TACTICAL INTELLIGENCE CLIENT TELEMETRY & CONTROLS
// =========================================================================

let currentPrintsData = [];
let autocompleteTimeout = null;

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

function closeAddCardModal() {
    const modal = document.getElementById("modal-add-card");
    if (modal) {
        modal.classList.add("hidden");
        document.getElementById("card-search-input").value = "";
        document.getElementById("autocomplete-dropdown").classList.add("hidden");
        document.getElementById("print-selector-container").classList.add("hidden");
        document.getElementById("btn-submit-add-card").disabled = true;
        currentPrintsData = [];
    }
}

function openEditTargetModal(id, name, currentTarget) {
    const modal = document.getElementById("modal-edit-target");
    if (!modal) return;

    document.getElementById("edit-target-item-id").value = id;
    document.getElementById("edit-target-card-name").textContent = `TARGET: ${name}`;
    document.getElementById("edit-target-price-input").value = currentTarget !== null ? currentTarget : "";
    modal.classList.remove("hidden");
    document.getElementById("edit-target-price-input").focus();
}

function closeEditTargetModal() {
    const modal = document.getElementById("modal-edit-target");
    if (modal) modal.classList.add("hidden");
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
            <option value="any" selected>★ Any Version (Lowest Market Price across all prints)</option>
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

    if (!currentPrintsData || currentPrintsData.length === 0) return;

    const selectedValue = printSelect ? printSelect.value : "any";
    const selectedFinish = finishSelect ? finishSelect.value : "nonfoil";

    if (selectedValue === "any") {
        // Any Version Mode
        const canonical = currentPrintsData[0];
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
    } else {
        // Specific Print Mode
        const printObj = currentPrintsData.find(p => p.id === selectedValue);
        if (!printObj) return;

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
        if (finishVal === "foil" && printObj.prices.usd_foil) {
            estPrice = `$${printObj.prices.usd_foil}`;
        } else if (finishVal === "etched" && printObj.prices.usd_etched) {
            estPrice = `$${printObj.prices.usd_etched}`;
        } else if (printObj.prices.usd) {
            estPrice = `$${printObj.prices.usd}`;
        }

        if (priceElem) priceElem.textContent = `TCGPLAYER MARKET TELEMETRY: ${estPrice}`;
    }
}

// =========================================================================
// Add Card Submit Action
// =========================================================================
async function submitAddCard() {
    const printSelect = document.getElementById("card-print-select");
    const finishSelect = document.getElementById("card-finish-select");
    const targetPriceInput = document.getElementById("card-target-price-input");
    const submitBtn = document.getElementById("btn-submit-add-card");

    if (!currentPrintsData || currentPrintsData.length === 0) {
        showToast("Search and select a card first.", "error");
        return;
    }

    const selectedValue = printSelect ? printSelect.value : "any";
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
// Edit Target Price Submit Action
// =========================================================================
async function submitEditTarget() {
    const itemId = document.getElementById("edit-target-item-id").value;
    const targetPriceVal = document.getElementById("edit-target-price-input").value;

    try {
        const res = await fetch(`/api/watchlist/update-target/${itemId}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                target_price: targetPriceVal ? parseFloat(targetPriceVal) : null,
            }),
        });

        const data = await res.json();
        if (res.ok) {
            showToast("Target threshold reconfigured.", "success");
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

    showToast("Executing fleet price surveillance scan...", "info");

    try {
        const res = await fetch("/api/watchlist/refresh-all", { method: "POST" });
        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Fleet telemetry synchronized", "success");
            setTimeout(() => window.location.reload(), 600);
        } else {
            showToast(data.error || "Fleet poll failed", "error");
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
    const cards = document.querySelectorAll(".watchlist-card");

    cards.forEach(card => {
        const name = card.dataset.name || "";
        const set = card.dataset.set || "";
        const isDeal = card.dataset.isDeal === "true";
        const inStock = card.dataset.inStock === "true";

        const matchesSearch = !searchVal || name.includes(searchVal) || set.includes(searchVal);
        let matchesFilter = true;

        if (dealFilter === "deals") {
            matchesFilter = isDeal;
        } else if (dealFilter === "in_stock") {
            matchesFilter = inStock;
        }

        if (matchesSearch && matchesFilter) {
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
    const btn = document.getElementById("btn-save-cadence");

    if (btn) {
        btn.disabled = true;
        btn.textContent = "Committing...";
    }

    try {
        const payload = {
            poll_interval_hours: select ? parseFloat(select.value) : 6.0,
            auto_poll_enabled: autoCheck ? autoCheck.checked : true,
        };

        const res = await fetch("/api/settings/cadence", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });

        const data = await res.json();
        if (res.ok) {
            showToast(data.message || "Surveillance cadence updated", "success");
            closeCadenceModal();
            await fetchCadenceTelemetry();
        } else {
            showToast(data.error || "Failed to update cadence", "error");
        }
    } catch (err) {
        console.error("Save cadence error:", err);
        showToast("Error updating cadence settings", "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = "Save Cadence";
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

