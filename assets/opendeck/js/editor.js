// ==========================================
// 5. BUILDER UI & EDITOR LOGIC
// ==========================================

function resizeImageForStorage(dataUrl, callback) {
    const img = new Image();
    img.onload = () => {
        const MAX_WIDTH = 1200;
        let width = img.width; let height = img.height;
        if (width > MAX_WIDTH) { height = Math.round((height * MAX_WIDTH) / width); width = MAX_WIDTH; }
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        canvas.getContext('2d').drawImage(img, 0, 0, width, height);
        callback(canvas.toDataURL('image/jpeg', 0.8));
    };
    img.src = dataUrl;
}

async function injectRandomImage(key, arrayName = null, index = null) {
    try {
        const res = await fetch('https://picsum.photos/1000/600');
        const blob = await res.blob();
        const reader = new FileReader();
        reader.onload = function (e) {
            if (arrayName !== null && index !== null) updateArrayItem(arrayName, index, key, e.target.result);
            else updateSlide(key, e.target.result);
            renderEditor();
        };
        reader.readAsDataURL(blob);
    } catch (e) { alert("Could not fetch random image."); }
}

function resizePreview() {
    const pane = document.getElementById('previewArea');
    const wrapper = document.getElementById('livePreview');
    if (!pane || !wrapper) return;
    const availableWidth = Math.max(240, pane.clientWidth - 80);
    const availableHeight = Math.max(180, pane.clientHeight - 120);
    const scaleX = availableWidth / 1200;
    const scaleY = availableHeight / 800;
    const scale = Math.max(0.2, Math.min(scaleX, scaleY, 1));
    wrapper.style.transform = `scale(${scale})`;
    requestAnimationFrame(() => fitSlideContent(wrapper.querySelector('.theme-slide')));
}
window.addEventListener('resize', resizePreview);

function fitSlideContent(root) {
    if (!root) return;
    const target = root.querySelector('[data-slide-autofit]');
    if (!target) return;

    const measureAutoFitBounds = (node) => {
        const baseRect = node.getBoundingClientRect();
        let minLeft = baseRect.left;
        let minTop = baseRect.top;
        let maxRight = baseRect.right;
        let maxBottom = baseRect.bottom;

        node.querySelectorAll('*').forEach((child) => {
            const style = window.getComputedStyle(child);
            if (style.display === 'none' || style.visibility === 'hidden' || style.position === 'fixed') return;

            const rect = child.getBoundingClientRect();
            if (!rect.width && !rect.height) return;

            const marginTop = parseFloat(style.marginTop) || 0;
            const marginRight = parseFloat(style.marginRight) || 0;
            const marginBottom = parseFloat(style.marginBottom) || 0;
            const marginLeft = parseFloat(style.marginLeft) || 0;

            minLeft = Math.min(minLeft, rect.left - marginLeft);
            minTop = Math.min(minTop, rect.top - marginTop);
            maxRight = Math.max(maxRight, rect.right + marginRight);
            maxBottom = Math.max(maxBottom, rect.bottom + marginBottom);
        });

        return {
            width: Math.max(1, maxRight - minLeft),
            height: Math.max(1, maxBottom - minTop)
        };
    };

    target.style.transform = 'none';
    target.style.transformOrigin = 'center center';

    const parentW = root.clientWidth  || 1200;
    const parentH = root.clientHeight || 800;
    const { width: contentW, height: contentH } = measureAutoFitBounds(target);
    const scale = Math.min(1, parentW / contentW, parentH / contentH);
    target.style.transform = scale < 1 ? `scale(${scale.toFixed(4)})` : 'none';
}

function getCollectionDensity(count) {
    if (count >= 10) return 'ultra';
    if (count >= 7) return 'tight';
    if (count >= 5) return 'compact';
    return '';
}

function getDensityClasses(count) {
    const density = getCollectionDensity(count);
    return {
        grid: density ? `od-density-${density}` : '',
        shell: density ? `od-shell-density-${density}` : ''
    };
}

function renderApp() {
    renderSlideList();
    renderEditor();
    renderPreview();
    document.getElementById('slideCountBadge').innerText = slides.length;
    // Fixes the large slide bug on initial load by waiting for the DOM to finish painting!
    setTimeout(resizePreview, 50);
}

let draggedIndex = null;
function renderSlideList() {
    const list = document.getElementById('slideList');
    list.innerHTML = '';
    slides.forEach((slide, index) => {
        const div = document.createElement('div');
        div.className = `slide-item rounded-lg mb-1 ${slide.id === currentSlideId ? 'active shadow-lg' : ''}`;
        div.draggable = true;

        div.ondragstart = (e) => { draggedIndex = index; e.dataTransfer.effectAllowed = 'move'; };
        div.ondragover = (e) => { e.preventDefault(); div.classList.add('drag-over'); };
        div.ondragleave = (e) => { div.classList.remove('drag-over'); };
        div.ondrop = (e) => {
            e.preventDefault(); div.classList.remove('drag-over');
            if (draggedIndex === null || draggedIndex === index) return;
            const item = slides.splice(draggedIndex, 1)[0];
            slides.splice(index, 0, item);
            saveProjects();
            renderApp();
        };
        div.onclick = () => { currentSlideId = slide.id; renderApp(); };

        div.innerHTML = `
            <div class="flex items-center gap-3 overflow-hidden w-full pointer-events-none">
                <div class="w-6 h-6 rounded ${slide.id === currentSlideId ? 'text-white' : 'bg-slate-800 text-slate-400'} flex items-center justify-center text-xs font-bold shrink-0" ${slide.id === currentSlideId ? 'style="background-color: var(--accent-color)"' : ''}>${index + 1}</div>
                <div class="truncate text-sm font-semibold flex-grow">${escapeHtml(slide.navName || 'Untitled')}</div>
            </div>
            <div class="flex shrink-0 pointer-events-auto">
                <button onclick="duplicateSlide('${slide.id}', event)" class="text-slate-500 hover:text-blue-400 p-2 rounded hover:bg-slate-800 transition" title="Duplicate Slide"><i class="fa-regular fa-copy"></i></button>
                <button onclick="deleteSlide('${slide.id}', event)" class="text-slate-500 hover:text-red-400 p-2 rounded hover:bg-slate-800 transition ml-1" title="Delete Slide"><i class="fa-solid fa-trash"></i></button>
            </div>
        `;
        list.appendChild(div);
    });
}

function handleImageUpload(event, key, arrayName = null, index = null) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function (e) {
        resizeImageForStorage(e.target.result, (resizedUrl) => {
            // This logic allows it to save to e.g. slide['icon'] OR slide['themes'][0]['icon']
            if (arrayName !== null && index !== null) {
                updateArrayItem(arrayName, index, key, resizedUrl);
            } else {
                updateSlide(key, resizedUrl);
            }
            renderEditor(); // Refresh the sidebar UI to show the new thumbnail
        });
    };
    reader.readAsDataURL(file);
}

function renderIconOrImage(val, classes) {
    if (!val) return '';
    if (val.startsWith('data:image')) {
        return `<img src="${val}" class="${classes} object-contain" style="max-height: 1em; width: auto; display: inline-block; vertical-align: middle;">`;
    }
    return `<i class="fa-solid ${escapeHtml(val)} ${classes}"></i>`;
}

// Updated input generator to handle file uploads for icon slots
function generateProIconInput(label, value, onUpdateStr, key, arrayName = null, index = null) {
    const randId = Math.random().toString(36).substr(2, 5);
    const inputId = `iconInput_${randId}`;
    const fileId = `fileInput_${randId}`;
    
    // Bridge the pick-icon modal back to the new logic
    window[`iconCb_${randId}`] = function (icon) {
        const input = document.getElementById(inputId);
        if (!input) return;
        input.value = icon;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        renderApp();
    };

    const updateArgs = arrayName ? `'${key}', '${arrayName}', ${index}` : `'${key}'`;
    const isImage = value && value.startsWith('data:image');

    return `
        <div class="flex flex-col gap-1.5 mb-3">
            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">${label}</label>
            <div class="flex gap-2 w-full items-center bg-[#020617] border border-slate-700 rounded-xl px-2 py-1.5 shadow-inner">
                <div class="w-8 h-8 flex items-center justify-center bg-slate-800 rounded-lg text-blue-400 shrink-0 border border-slate-600 overflow-hidden">
                    ${isImage ? `<img src="${value}" class="w-full h-full object-cover">` : `<i class="fa-solid ${escapeHtml(value)} text-sm"></i>`}
                </div>
                <input type="text" id="${inputId}" class="hidden" value="${escapeHtml(value)}" oninput="${onUpdateStr}">
                <input type="file" id="${fileId}" class="hidden" accept="image/*" onchange="handleImageUpload(event, ${updateArgs})">
                
                <button class="bg-slate-800 hover:bg-slate-700 text-white text-[9px] font-bold px-2 py-2 rounded-lg border border-slate-700 transition-colors uppercase tracking-tighter" 
                        onclick="document.getElementById('${fileId}').click()">Upload File</button>
                <div class="h-4 w-px bg-slate-700"></div>
                <button class="bg-slate-800 hover:bg-slate-700 text-white text-[9px] font-bold px-2 py-2 rounded-lg border border-slate-700 transition-colors uppercase tracking-tighter" 
                        onclick="openIconModal(window.iconCb_${randId})">Pick Icon</button>
            </div>
        </div>
    `;
}

const inspectorDefaultFonts = [
    { name: 'Inter (Modern Sans)', value: "'Inter', sans-serif" },
    { name: 'Roboto (Clean Sans)', value: "'Roboto', sans-serif" },
    { name: 'Space Grotesk (Tech Sans)', value: "'Space Grotesk', sans-serif" },
    { name: 'Playfair Display (Elegant Serif)', value: "'Playfair Display', serif" }
];

function getPresentationFontOptions() {
    const baseOptions = inspectorDefaultFonts.map((font) =>
        `<option value="${font.value}" ${globalSettings.font === font.value ? 'selected' : ''}>${font.name}</option>`
    );

    const customOptions = (globalSettings.savedFonts || []).map((font) =>
        `<option value="${font.family}" ${globalSettings.font === font.family ? 'selected' : ''}>${escapeHtml(font.name)}</option>`
    );

    if (!customOptions.length) return baseOptions.join('');

    return `${baseOptions.join('')}<optgroup label="Your Custom Fonts">${customOptions.join('')}</optgroup>`;
}

function handlePresentationLogoUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = function (e) {
        resizeImageForStorage(e.target.result, (resizedUrl) => {
            updateGlobalSetting('companyLogo', resizedUrl);
            renderEditor();
        });
    };
    reader.readAsDataURL(file);
}

function removePresentationLogo() {
    updateGlobalSetting('companyLogo', '');
    renderEditor();
}

// --- 🔥 HIGH-END PURE TAILWIND PRO INSPECTOR ---
function renderEditor() {
    const form = document.getElementById('editorForm');
    const slide = slides.find(s => s.id === currentSlideId);
    if (!slide) { form.innerHTML = ''; return; }

    const slideIndex = slides.findIndex(s => s.id === currentSlideId);
    const slideTypeLabels = {
        intro: 'Title Hero',
        split: 'Side-by-Side',
        grid: 'Feature Grid',
        list: 'Checklist',
        code: 'Code Window',
        cta: 'Call To Action',
        glass_intro: 'Glass Hero',
        comparison: 'Comparison Panels',
        showcase_window: 'Showcase Window',
        roadmap_cards: 'Roadmap Cards',
        corp_title: 'Executive Title',
        corp_quote: 'Quote Slide',
        corp_image_text: 'Magazine Layout',
        corp_basic: 'Title & Content',
        corp_team: 'Team Layout',
        pitch_hero: 'Cinematic Hero',
        pitch_stats: 'Metrics',
        pitch_timeline: 'Timeline',
        pitch_pricing: 'Pricing',
        res_title: 'Research Title',
        res_abstract: 'Abstract/Summary',
        res_methodology: 'Methodology Flow',
        res_data: 'Data Insight',
        res_citations: 'Bibliography',
        res_lit_review: 'Literature Review',
        res_variables: 'Variables & Hypotheses',
        res_limits: 'Scope & Limitations',
        res_collaborators: 'Collaborators',
        res_key_takeaway: 'Major Conclusion',
        custom_block_layout: 'Custom Layout'
};
    const slideTypeLabel = slideTypeLabels[slide.type] || 'Custom Slide';
    const slideCapacityHint = getSlideCapacityHint(slide);

    let html = `
        <div class="inspector-card inspector-card--hero">
            <div class="inspector-card__header inspector-card__header--hero">
                <div class="flex items-start justify-between gap-4 w-full">
                    <div>
                        <div class="inspector-eyebrow">Slide ${slideIndex + 1}</div>
                        <div class="text-sm font-extrabold text-white tracking-wide">${slideTypeLabel}</div>
                        ${slideCapacityHint ? `<div class="mt-1 text-[0.65rem] font-bold uppercase tracking-[0.16em] text-blue-300/80">${slideCapacityHint}</div>` : ''}
                    </div>
                    <div class="inspector-chip">${escapeHtml(slide.transition || 'fade-in')}</div>
                </div>
            </div>
            <div class="inspector-card__body space-y-4">
                <div class="flex flex-col gap-2">
                    <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Outline Label</label>
                    <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-xl px-3 py-2.5 text-sm text-white outline-none transition-colors shadow-inner" value="${escapeHtml(slide.navName || '')}" oninput="updateSlide('navName', this.value)">
                </div>
                <div class="flex gap-3">
                    <div class="flex flex-col gap-2 w-1/2">
                        <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Transition</label>
                        <select class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-2 py-2 text-xs text-white outline-none transition-colors cursor-pointer" onchange="updateSlide('transition', this.value)">
                            <option value="fade-in" ${slide.transition === 'fade-in' ? 'selected' : ''}>Fade In</option>
                            <option value="slide-up" ${slide.transition === 'slide-up' ? 'selected' : ''}>Slide Up</option>
                            <option value="zoom-in" ${slide.transition === 'zoom-in' ? 'selected' : ''}>Zoom In</option>
                            <option value="reveal-right" ${slide.transition === 'reveal-right' ? 'selected' : ''}>Reveal Right</option>
                        </select>
                    </div>
                    <div class="flex flex-col gap-2 w-1/2">
                        <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Background</label>
                        <select class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-2 py-2 text-xs text-white outline-none transition-colors cursor-pointer" onchange="updateSlide('bgOverride', this.value)">
                            <option value="bg-default" ${slide.bgOverride === 'bg-default' ? 'selected' : ''}>Dark Radial</option>
                            <option value="bg-deepblue" ${slide.bgOverride === 'bg-deepblue' ? 'selected' : ''}>Deep Blue</option>
                            <option value="bg-midnight" ${slide.bgOverride === 'bg-midnight' ? 'selected' : ''}>Midnight</option>
                            <option value="bg-aurora" ${slide.bgOverride === 'bg-aurora' ? 'selected' : ''}>Aurora Glass</option>
                            <option value="bg-sunset" ${slide.bgOverride === 'bg-sunset' ? 'selected' : ''}>Sunset Glow</option>
                            <option value="bg-pitchblack" ${slide.bgOverride === 'bg-pitchblack' ? 'selected' : ''}>Solid Black</option>
                            <option value="bg-purewhite" ${slide.bgOverride === 'bg-purewhite' ? 'selected' : ''}>Pure White</option>
                            <option value="bg-academic" ${slide.bgOverride === 'bg-academic' ? 'selected' : ''}>Academic Ivory</option>
                            <option value="bg-blueprint" ${slide.bgOverride === 'bg-blueprint' ? 'selected' : ''}>Technical Blueprint</option>
                            <option value="bg-forest" ${slide.bgOverride === 'bg-forest' ? 'selected' : ''}>Deep Emerald</option>
                            <option value="bg-nebula" ${slide.bgOverride === 'bg-nebula' ? 'selected' : ''}>Space Nebula</option>
                        </select>
                    </div>
                </div>
                <p class="inspector-helper-copy">Click directly on the canvas to edit content. Use this panel for structure, media, styling, and presenter notes.</p>
            </div>
        </div>
    `;

    const openBlock = (title, icon) => `<div class="inspector-card"><div class="inspector-card__header"><div class="flex items-center"><i class="fa-solid ${icon} text-blue-400 mr-2 text-xs"></i><span class="text-[0.65rem] font-extrabold uppercase tracking-[0.18em] text-slate-300">${title}</span></div><div class="inspector-dot"></div></div><div class="inspector-card__body">`;
    const closeBlock = `</div></div>`;
    let presentationSettings = ``;

    presentationSettings = openBlock('Presentation Settings', 'fa-palette') + `
        <div class="text-[10px] uppercase tracking-[0.18em] text-slate-500 mb-4">Branding for this presentation only. The tool UI will always use Space Grotesque.</div>
        <div class="flex flex-col gap-2 mb-4">
            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Theme Color</label>
            <div class="flex items-center gap-3">
                <input type="color" value="${escapeHtml(globalSettings.theme || '#3B82F6')}" onchange="updateGlobalSetting('theme', this.value)"
                    class="w-12 h-10 rounded-lg cursor-pointer bg-slate-900 border border-slate-700 p-0 shrink-0">
                <span class="text-[11px] text-slate-500">Used for accents, buttons, and template graphics.</span>
            </div>
        </div>
        <div class="flex flex-col gap-2 mb-4">
            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Font Family (Slides Only)</label>
            <select onchange="updateGlobalSetting('font', this.value)"
                class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2.5 text-xs text-white outline-none transition-colors cursor-pointer font-bold">
                ${getPresentationFontOptions()}
            </select>
            <p class="text-[10px] text-slate-500 mt-1">Changes the font inside slides only. The editor UI stays on Space Grotesque.</p>
        </div>
        <div class="flex flex-col gap-2 mb-4">
            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Top Navigation Header Text</label>
            <input type="text" value="${escapeHtml(globalSettings.headerText || 'OpenDeck')}" oninput="updateGlobalSetting('headerText', this.value)"
                class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-xl px-3 py-2.5 text-sm text-white outline-none transition-colors shadow-inner">
        </div>
        <div class="flex gap-3 mb-4">
            <div class="flex flex-col gap-2 w-24 shrink-0">
                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Nav Icon</label>
                <input type="text" maxlength="2" value="${escapeHtml(globalSettings.headerIcon || 'OD')}" oninput="updateGlobalSetting('headerIcon', this.value.toUpperCase())"
                    class="w-full text-center bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-xl px-3 py-2.5 text-sm text-white outline-none transition-colors shadow-inner font-bold uppercase">
            </div>
            <div class="flex flex-col gap-2 flex-grow">
                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Watermark</label>
                <div class="flex items-center gap-3">
                    <input type="file" accept="image/*" onchange="handlePresentationLogoUpload(event)"
                        class="w-full text-[10px] text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-2 file:py-1.5 file:rounded file:cursor-pointer file:hover:bg-slate-700">
                    ${globalSettings.companyLogo ? `<button onclick="removePresentationLogo()" class="text-[11px] text-red-400 hover:text-red-300 transition font-bold shrink-0">Remove</button>` : ''}
                </div>
                <div class="text-[10px] text-slate-500">Applied to slides only.</div>
                ${globalSettings.companyLogo ? `<img src="${globalSettings.companyLogo}" class="h-10 w-auto max-w-[120px] object-contain rounded border border-slate-700 bg-white/95 p-1">` : `<div class="text-[10px] italic text-slate-600">No watermark uploaded</div>`}
            </div>
        </div>
        <button onclick="openSettingsModal()" class="w-full bg-slate-800 hover:bg-slate-700 border border-slate-700 text-white text-xs font-bold py-2.5 rounded-lg transition-colors">
            <i class="fa-solid fa-gear mr-2 text-blue-400"></i>Advanced Settings
        </button>
    ` + closeBlock;

    if (slide.type === 'intro' || slide.type === 'res_title') {
        const tagsLimit = getArrayLimit(slide, 'tags');
        const reachedTagsLimit = tagsLimit && (slide.tags || []).length >= tagsLimit.limit;
        html += openBlock('Hero Assets', 'fa-image');
        html += generateProIconInput('Main Icon', slide.icon, "updateSlide('icon', this.value)", 'icon');
        html += closeBlock;

        html += openBlock('Pill Badges', 'fa-tags');
        (slide.tags || []).forEach((tag, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all shadow-md border border-slate-600" onclick="removeArrayItem('tags', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="flex items-center justify-between mb-3">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Tag Color</label>
                            <input type="color" class="w-8 h-6 bg-transparent rounded cursor-pointer border-0 p-0" value="${tag.color || '#3B82F6'}" onchange="updateArrayItem('tags', ${i}, 'color', this.value)">
                        </div>
                        ${generateProIconInput('Icon', tag.icon, `updateArrayItem('tags', ${i}, 'icon', this.value)`)}
                     </div>`;
        });
        if (reachedTagsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Title Hero supports up to ${tagsLimit.limit} ${tagsLimit.label} per slide to guarantee fit on all screens. Create another ${tagsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedTagsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('tags', {text:'New Tag', icon:'fa-star', color:'#3B82F6'})" ${reachedTagsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Tag</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'split' || slide.type === 'res_abstract') {
        const splitBulletsLimit = getArrayLimit(slide, 'bullets');
        const reachedSplitBulletsLimit = splitBulletsLimit && (slide.bullets || []).length >= splitBulletsLimit.limit;
        html += openBlock('Right Visual', 'fa-image');
        if (slide.image) {
            html += `<img src="${slide.image}" class="w-full h-24 object-cover rounded-lg border border-slate-700 mb-3 shadow-inner">
                     <button class="w-full bg-red-900/30 hover:bg-red-500 text-red-400 hover:text-white border border-red-900/50 text-xs font-bold py-2 rounded-lg transition" onclick="removeImage('image')">Remove Image</button>`;
        } else {
            html += `<div class="flex justify-between items-center mb-2"><label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Upload Image</label><button class="text-[9px] bg-blue-600/20 text-blue-400 hover:bg-blue-600 hover:text-white px-2 py-0.5 rounded transition" onclick="injectRandomImage('image')">Random</button></div>
                     <input type="file" class="w-full text-xs text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-3 file:py-1.5 file:rounded file:cursor-pointer file:hover:bg-slate-700 mb-4" accept="image/*" onchange="handleImageUpload(event, 'image')">
                     <div class="flex items-center gap-3 mb-4"><div class="h-px bg-slate-800 flex-grow"></div><span class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">OR USE ICON</span><div class="h-px bg-slate-800 flex-grow"></div></div>
                     ${generateProIconInput('Highlight Box Icon', slide.boxIcon, "updateSlide('boxIcon', this.value)")}`;
        }
        html += closeBlock;

        html += openBlock('Left Details', 'fa-list');
        (slide.bullets || []).forEach((b, i) => {
            html += `<div class="flex justify-between items-center bg-[#0b1121] border border-slate-700/50 rounded-lg p-2 mb-2">
                        <span class="text-xs font-bold text-slate-500 px-2">${i + 1}</span>
                        <button class="text-slate-500 hover:text-red-400 px-2 transition-colors" onclick="removeArrayPrimitive('bullets', ${i})"><i class="fa-solid fa-trash"></i></button>
                     </div>`;
        });
        if (reachedSplitBulletsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Side-by-Side supports up to ${splitBulletsLimit.limit} ${splitBulletsLimit.label} per slide to guarantee fit on all screens. Create another ${splitBulletsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors mt-2 ${reachedSplitBulletsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayPrimitive('bullets', 'New key point')" ${reachedSplitBulletsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Bullet</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'grid') {
        const cardsLimit = getArrayLimit(slide, 'cards');
        const reachedCardsLimit = cardsLimit && (slide.cards || []).length >= cardsLimit.limit;
        html += openBlock('Feature Cards', 'fa-border-all');
        (slide.cards || []).forEach((card, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 text-slate-400 hover:bg-red-500 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all border border-slate-600 shadow-md" onclick="removeArrayItem('cards', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="text-[10px] font-extrabold text-slate-500 tracking-widest mb-3 border-b border-slate-800 pb-1">CARD ${i + 1}</div>`;
            if (card.image) {
                html += `<img src="${card.image}" class="w-full h-12 object-cover rounded mb-2 border border-slate-700">
                         <button class="text-[10px] text-red-400 hover:text-red-300 w-full text-left font-bold" onclick="removeImage('image', 'cards', ${i})">Remove Image</button>`;
            } else {
                html += `<div class="flex items-center justify-between mb-3">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Accent Color</label>
                            <input type="color" class="w-8 h-6 bg-transparent rounded cursor-pointer border-0 p-0" value="${card.color || '#3B82F6'}" onchange="updateArrayItem('cards', ${i}, 'color', this.value)">
                        </div>
                        ${generateProIconInput('Card Icon', card.icon, `updateArrayItem('cards', ${i}, 'icon', this.value)`)}
                        <div class="flex justify-between items-center mt-3 border-t border-slate-800 pt-3 mb-2">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Upload Image</label>
                            <button class="text-[9px] bg-slate-800 hover:bg-blue-600 text-slate-300 hover:text-white px-2 py-0.5 rounded transition" onclick="injectRandomImage('image', 'cards', ${i})">Random</button>
                        </div>
                        <input type="file" class="w-full text-[10px] text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-2 file:py-1 file:rounded file:cursor-pointer file:hover:bg-slate-700" accept="image/*" onchange="handleImageUpload(event, 'image', 'cards', ${i})">`;
            }
            html += `</div>`;
        });
        if (reachedCardsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Feature Grid supports up to ${cardsLimit.limit} ${cardsLimit.label} per slide to guarantee fit on all screens. Create another ${cardsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedCardsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('cards', {title:'New Feature', text:'Description', icon:'fa-star', color:'#3B82F6'})" ${reachedCardsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Card</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'list' || slide.type === 'res_citations') {
        const itemsLimit = getArrayLimit(slide, 'items');
        const reachedItemsLimit = itemsLimit && (slide.items || []).length >= itemsLimit.limit;
        html += openBlock('Status Rows', 'fa-list-check');
        (slide.items || []).forEach((item, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all border border-slate-600 shadow-md" onclick="removeArrayItem('items', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="flex gap-4 mb-4 mt-2">
                            <div class="flex-grow flex flex-col gap-1.5">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Status Label</label>
                                <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2 text-xs text-white outline-none transition-colors" value="${escapeHtml(item.value)}" oninput="updateArrayItem('items', ${i}, 'value', this.value)" placeholder="e.g. DONE">
                            </div>
                            <div class="flex flex-col gap-1.5 w-16">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Color</label>
                                <input type="color" class="w-full h-8 bg-transparent rounded cursor-pointer border-0 p-0" value="${item.color || '#10B981'}" onchange="updateArrayItem('items', ${i}, 'color', this.value)">
                            </div>
                        </div>
                        ${generateProIconInput('Row Icon', item.icon, `updateArrayItem('items', ${i}, 'icon', this.value)`)}
                     </div>`;
        });
        if (reachedItemsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Checklist supports up to ${itemsLimit.limit} ${itemsLimit.label} per slide to guarantee fit on all screens. Create another ${itemsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedItemsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('items', {label:'New Item', value:'WAITING', icon:'fa-circle-dot', color:'#F59E0B'})" ${reachedItemsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Row</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'code') {
        html += openBlock('Terminal Settings', 'fa-terminal');
        html += `<div class="flex flex-col gap-2">
                    <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Syntax Color Theme</label>
                    <select class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2 text-xs text-white outline-none cursor-pointer" onchange="updateSlide('codeColor', this.value)">
                        <option value="text-green-400" ${slide.codeColor === 'text-green-400' ? 'selected' : ''}>Hacker Green</option>
                        <option value="text-blue-400" ${slide.codeColor === 'text-blue-400' ? 'selected' : ''}>Ocean Blue</option>
                        <option value="text-pink-400" ${slide.codeColor === 'text-pink-400' ? 'selected' : ''}>Synthwave Pink</option>
                        <option value="text-yellow-400" ${slide.codeColor === 'text-yellow-400' ? 'selected' : ''}>Warning Yellow</option>
                        <option value="text-white" ${slide.codeColor === 'text-white' ? 'selected' : ''}>Plain White</option>
                    </select>
                 </div>`;
        html += closeBlock;
    }
    else if (slide.type === 'cta') {
        html += openBlock('Hero Visual', 'fa-rocket');
        if (slide.image) {
            html += `<img src="${slide.image}" class="w-full h-24 object-cover rounded-lg border border-slate-700 mb-3 shadow-inner">
                     <button class="w-full bg-red-900/30 hover:bg-red-500 text-red-400 hover:text-white border border-red-900/50 text-xs font-bold py-2 rounded-lg transition" onclick="removeImage('image')">Remove Image</button>`;
        } else {
            html += `${generateProIconInput('Main Icon', slide.icon, "updateSlide('icon', this.value)")}
                     <div class="flex justify-between items-center mb-2 mt-4 border-t border-slate-800 pt-4"><label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Or Upload Image</label><button class="text-[9px] bg-blue-600/20 text-blue-400 hover:bg-blue-600 hover:text-white px-2 py-0.5 rounded transition" onclick="injectRandomImage('image')">Random</button></div>
                     <input type="file" class="w-full text-xs text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-3 file:py-1.5 file:rounded file:cursor-pointer file:hover:bg-slate-700" accept="image/*" onchange="handleImageUpload(event, 'image')">`;
        }
        html += closeBlock;
    }
    else if (slide.type === 'glass_intro') {
        const badgesLimit = getArrayLimit(slide, 'badges');
        const reachedBadgesLimit = badgesLimit && (slide.badges || []).length >= badgesLimit.limit;
        html += openBlock('Hero Identity', 'fa-wand-magic-sparkles');
        html += generateProIconInput('Main Icon', slide.icon, "updateSlide('icon', this.value)", 'icon');
        html += closeBlock;

        html += openBlock('Support Badges', 'fa-tags');
        (slide.badges || []).forEach((badge, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all shadow-md border border-slate-600" onclick="removeArrayItem('badges', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="flex items-center justify-between mb-3">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Badge Color</label>
                            <input type="color" class="w-8 h-6 bg-transparent rounded cursor-pointer border-0 p-0" value="${badge.color || '#3B82F6'}" onchange="updateArrayItem('badges', ${i}, 'color', this.value)">
                        </div>
                        ${generateProIconInput('Badge Icon', badge.icon, `updateArrayItem('badges', ${i}, 'icon', this.value)`)}
                     </div>`;
        });
        if (reachedBadgesLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Glass Hero supports up to ${badgesLimit.limit} ${badgesLimit.label} per slide to guarantee fit on all screens. Create another ${badgesLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors shadow-sm ${reachedBadgesLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('badges', {text:'New Badge', icon:'fa-star', color:'#3B82F6'})" ${reachedBadgesLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Badge</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'comparison') {
        const leftPointsLimit = getArrayLimit(slide, 'leftPoints');
        const reachedLeftPointsLimit = leftPointsLimit && (slide.leftPoints || []).length >= leftPointsLimit.limit;
        const rightPointsLimit = getArrayLimit(slide, 'rightPoints');
        const reachedRightPointsLimit = rightPointsLimit && (slide.rightPoints || []).length >= rightPointsLimit.limit;
        html += openBlock('Left Panel', 'fa-arrow-left');
        html += generateProIconInput('Left Icon', slide.leftIcon, "updateSlide('leftIcon', this.value)");
        (slide.leftPoints || []).forEach((point, i) => {
            html += `<div class="flex justify-between items-center bg-[#0b1121] border border-slate-700/50 rounded-lg p-2 mb-2">
                        <span class="text-xs font-bold text-slate-500 px-2">${i + 1}</span>
                        <button class="text-slate-500 hover:text-red-400 px-2 transition-colors" onclick="removeArrayPrimitive('leftPoints', ${i})"><i class="fa-solid fa-trash"></i></button>
                     </div>`;
        });
        if (reachedLeftPointsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Comparison Panels supports up to ${leftPointsLimit.limit} ${leftPointsLimit.label} per slide to guarantee fit on all screens. Create another ${leftPointsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors mt-2 ${reachedLeftPointsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayPrimitive('leftPoints', 'New point')" ${reachedLeftPointsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Left Point</button>`;
        html += closeBlock;

        html += openBlock('Right Panel', 'fa-arrow-right');
        html += generateProIconInput('Right Icon', slide.rightIcon, "updateSlide('rightIcon', this.value)");
        (slide.rightPoints || []).forEach((point, i) => {
            html += `<div class="flex justify-between items-center bg-[#0b1121] border border-slate-700/50 rounded-lg p-2 mb-2">
                        <span class="text-xs font-bold text-slate-500 px-2">${i + 1}</span>
                        <button class="text-slate-500 hover:text-red-400 px-2 transition-colors" onclick="removeArrayPrimitive('rightPoints', ${i})"><i class="fa-solid fa-trash"></i></button>
                     </div>`;
        });
        if (reachedRightPointsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Comparison Panels supports up to ${rightPointsLimit.limit} ${rightPointsLimit.label} per slide to guarantee fit on all screens. Create another ${rightPointsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors mt-2 ${reachedRightPointsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayPrimitive('rightPoints', 'New point')" ${reachedRightPointsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Right Point</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'showcase_window') {
        const showcaseBulletsLimit = getArrayLimit(slide, 'bullets');
        const reachedShowcaseBulletsLimit = showcaseBulletsLimit && (slide.bullets || []).length >= showcaseBulletsLimit.limit;
        html += openBlock('Narrative Bullets', 'fa-list');
        (slide.bullets || []).forEach((bullet, i) => {
            html += `<div class="flex justify-between items-center bg-[#0b1121] border border-slate-700/50 rounded-lg p-2 mb-2">
                        <span class="text-xs font-bold text-slate-500 px-2">${i + 1}</span>
                        <button class="text-slate-500 hover:text-red-400 px-2 transition-colors" onclick="removeArrayPrimitive('bullets', ${i})"><i class="fa-solid fa-trash"></i></button>
                     </div>`;
        });
        if (reachedShowcaseBulletsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Showcase Window supports up to ${showcaseBulletsLimit.limit} ${showcaseBulletsLimit.label} per slide to guarantee fit on all screens. Create another ${showcaseBulletsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors mt-2 ${reachedShowcaseBulletsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayPrimitive('bullets', 'New supporting point')" ${reachedShowcaseBulletsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Bullet</button>`;
        html += closeBlock;

        html += openBlock('Window Styling', 'fa-window-maximize');
        html += `<div class="flex flex-col gap-2 mb-4">
                    <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Code Color Theme</label>
                    <select class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2 text-xs text-white outline-none cursor-pointer" onchange="updateSlide('codeColor', this.value)">
                        <option value="text-green-400" ${slide.codeColor === 'text-green-400' ? 'selected' : ''}>Hacker Green</option>
                        <option value="text-blue-400" ${slide.codeColor === 'text-blue-400' ? 'selected' : ''}>Ocean Blue</option>
                        <option value="text-pink-400" ${slide.codeColor === 'text-pink-400' ? 'selected' : ''}>Synthwave Pink</option>
                        <option value="text-yellow-400" ${slide.codeColor === 'text-yellow-400' ? 'selected' : ''}>Warning Yellow</option>
                        <option value="text-white" ${slide.codeColor === 'text-white' ? 'selected' : ''}>Plain White</option>
                    </select>
                 </div>`;
        html += closeBlock;
    }
    else if (slide.type === 'res_methodology') {
    html += openBlock('Methodology Stages', 'fa-timeline');
    (slide.phases || []).forEach((phase, i) => {
        html += `
            <div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group">
                <div class="text-[10px] font-bold text-slate-500 uppercase mb-2">Stage ${i+1}</div>
                ${generateProIconInput('Stage Icon', phase.icon, `updateArrayItem('phases', ${i}, 'icon', this.value)`, 'icon', 'phases', i)}
                <div class="flex gap-2">
                    <input type="color" class="w-10 h-8 bg-transparent" value="${phase.color}" onchange="updateArrayItem('phases', ${i}, 'color', this.value)">
                    <input type="text" class="flex-grow bg-slate-900 text-xs p-2 rounded" value="${phase.status}" oninput="updateArrayItem('phases', ${i}, 'status', this.value)">
                </div>
            </div>
        `;
    });
    html += closeBlock;
}
    else if (slide.type === 'pitch_stats' || slide.type === 'res_data') {
        const statsLimit = getArrayLimit(slide, 'stats');
        const reachedStatsLimit = statsLimit && (slide.stats || []).length >= statsLimit.limit;
        html += openBlock('Key Metrics', 'fa-chart-simple');
        (slide.stats || []).forEach((stat, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all border border-slate-600 shadow-md" onclick="removeArrayItem('stats', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="flex gap-4 mb-4 mt-2">
                            <div class="flex-grow flex flex-col gap-1.5">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Value (e.g. 10x)</label>
                                <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2 text-xs text-white outline-none" value="${escapeHtml(stat.value)}" oninput="updateArrayItem('stats', ${i}, 'value', this.value)">
                            </div>
                            <div class="flex flex-col gap-1.5 w-16">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Color</label>
                                <input type="color" class="w-full h-8 bg-transparent rounded cursor-pointer border-0 p-0" value="${stat.color || '#3B82F6'}" onchange="updateArrayItem('stats', ${i}, 'color', this.value)">
                            </div>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Label</label>
                            <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2 text-xs text-white outline-none" value="${escapeHtml(stat.label)}" oninput="updateArrayItem('stats', ${i}, 'label', this.value)">
                        </div>
                     </div>`;
        });
        if (reachedStatsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Metrics supports up to ${statsLimit.limit} ${statsLimit.label} per slide to guarantee fit on all screens. Create another ${statsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedStatsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('stats', {value:'100', label:'Metric', color:'#3B82F6'})" ${reachedStatsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Metric</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'pitch_timeline') {
        const timelineLimit = getArrayLimit(slide, 'timeline');
        const reachedTimelineLimit = timelineLimit && (slide.timeline || []).length >= timelineLimit.limit;
        html += openBlock('Timeline Milestones', 'fa-timeline');
        (slide.timeline || []).forEach((item, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all border border-slate-600 shadow-md" onclick="removeArrayItem('timeline', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="flex gap-4 mb-4 mt-2">
                            <div class="flex-grow flex flex-col gap-1.5">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Milestone</label>
                                <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-2 text-xs text-white outline-none" value="${escapeHtml(item.year)}" oninput="updateArrayItem('timeline', ${i}, 'year', this.value)">
                            </div>
                            <div class="flex flex-col gap-1.5 w-16">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Color</label>
                                <input type="color" class="w-full h-8 bg-transparent rounded cursor-pointer border-0 p-0" value="${item.color || '#3B82F6'}" onchange="updateArrayItem('timeline', ${i}, 'color', this.value)">
                            </div>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Description</label>
                            <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-1.5 text-xs text-white outline-none" value="${escapeHtml(item.text)}" oninput="updateArrayItem('timeline', ${i}, 'text', this.value)">
                        </div>
                     </div>`;
        });
        if (reachedTimelineLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Timeline supports up to ${timelineLimit.limit} ${timelineLimit.label} per slide to guarantee fit on all screens. Create another ${timelineLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedTimelineLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('timeline', {year:'2025', text:'Next Phase', color:'#3B82F6'})" ${reachedTimelineLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Milestone</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'corp_image_text') {
        const editorialBulletsLimit = getArrayLimit(slide, 'bullets');
        const reachedEditorialBulletsLimit = editorialBulletsLimit && (slide.bullets || []).length >= editorialBulletsLimit.limit;
        html += openBlock('Left Visual', 'fa-image');
        if (slide.image) {
            html += `<img src="${slide.image}" class="w-full h-24 object-cover rounded-lg border border-slate-700 mb-3 shadow-inner">
                     <button class="w-full bg-red-900/30 hover:bg-red-500 text-red-400 hover:text-white border border-red-900/50 text-xs font-bold py-2 rounded-lg transition" onclick="removeImage('image')">Remove Image</button>`;
        } else {
            html += `<div class="flex justify-between items-center mb-2"><label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Upload Image</label><button class="text-[9px] bg-blue-600/20 text-blue-400 hover:bg-blue-600 hover:text-white px-2 py-0.5 rounded transition" onclick="injectRandomImage('image')">Random</button></div>
                     <input type="file" class="w-full text-xs text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-3 file:py-1.5 file:rounded file:cursor-pointer file:hover:bg-slate-700 mb-2" accept="image/*" onchange="handleImageUpload(event, 'image')">`;
        }
        html += closeBlock;

        html += openBlock('Right Editorial Bullets', 'fa-list');
        (slide.bullets || []).forEach((b, i) => {
            html += `<div class="flex justify-between items-center bg-[#0b1121] border border-slate-700/50 rounded-lg p-2 mb-2">
                        <span class="text-xs font-bold text-slate-500 px-2">${i + 1}</span>
                        <button class="text-slate-500 hover:text-red-400 px-2 transition-colors" onclick="removeArrayPrimitive('bullets', ${i})"><i class="fa-solid fa-trash"></i></button>
                     </div>`;
        });
        if (reachedEditorialBulletsLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Magazine Layout supports up to ${editorialBulletsLimit.limit} ${editorialBulletsLimit.label} per slide to guarantee fit on all screens. Create another ${editorialBulletsLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors mt-2 ${reachedEditorialBulletsLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayPrimitive('bullets', 'New editorial point')" ${reachedEditorialBulletsLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Point</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'pitch_hero') {
        html += openBlock('Background Image', 'fa-image');
        if (slide.image) {
            html += `<img src="${slide.image}" class="w-full h-24 object-cover rounded-lg border border-slate-700 mb-3 shadow-inner">
                     <button class="w-full bg-red-900/30 hover:bg-red-500 text-red-400 hover:text-white border border-red-900/50 text-xs font-bold py-2 rounded-lg transition" onclick="removeImage('image')">Remove</button>`;
        } else {
            html += `<div class="flex justify-between items-center mb-2"><label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Upload</label><button class="text-[9px] bg-blue-600/20 text-blue-400 hover:bg-blue-600 hover:text-white px-2 py-0.5 rounded transition" onclick="injectRandomImage('image')">Random</button></div>
                     <input type="file" class="w-full text-xs text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-3 file:py-1.5 file:rounded file:cursor-pointer file:hover:bg-slate-700" accept="image/*" onchange="handleImageUpload(event, 'image')">`;
        }
        html += closeBlock;
    }
    else if (slide.type === 'corp_team') {
        const teamLimit = getArrayLimit(slide, 'team');
        const reachedTeamLimit = teamLimit && (slide.team || []).length >= teamLimit.limit;
        html += openBlock('Team Members', 'fa-users');
        (slide.team || []).forEach((member, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all border border-slate-600 shadow-md" onclick="removeArrayItem('team', ${i})"><i class="fa-solid fa-trash"></i></div>
                        ${member.image ? `<img src="${member.image}" class="w-16 h-16 object-cover rounded-full mx-auto mb-3 border-2 border-slate-700"> <button class="w-full bg-red-900/30 hover:bg-red-500 text-red-400 hover:text-white border border-red-900/50 text-[10px] font-bold py-1.5 rounded transition mb-3" onclick="removeImage('image', 'team', ${i})">Remove</button>` : `<div class="flex justify-between items-center mb-2"><label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Avatar</label><button class="text-[9px] bg-slate-800 hover:bg-blue-600 text-slate-300 hover:text-white px-2 py-0.5 rounded transition" onclick="injectRandomImage('image', 'team', ${i})">Random</button></div> <input type="file" class="w-full text-[10px] text-slate-400 file:bg-slate-800 file:border-0 file:text-white file:px-2 file:py-1 file:rounded file:cursor-pointer file:hover:bg-slate-700 mb-3" accept="image/*" onchange="handleImageUpload(event, 'image', 'team', ${i})">`}
                        <div class="flex flex-col gap-1.5 mb-2">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Name</label>
                            <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-1.5 text-xs text-white outline-none" value="${escapeHtml(member.name)}" oninput="updateArrayItem('team', ${i}, 'name', this.value)">
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Role</label>
                            <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-1.5 text-xs text-white outline-none" value="${escapeHtml(member.role)}" oninput="updateArrayItem('team', ${i}, 'role', this.value)">
                        </div>
                     </div>`;
        });
        if (reachedTeamLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Team Layout supports up to ${teamLimit.limit} ${teamLimit.label} per slide to guarantee fit on all screens. Create another ${teamLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedTeamLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('team', {name:'New Member', role:'Job Title', image:''})" ${reachedTeamLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Member</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'pitch_pricing') {
        const pricingLimit = getArrayLimit(slide, 'tiers');
        const reachedPricingLimit = pricingLimit && (slide.tiers || []).length >= pricingLimit.limit;
        html += openBlock('Pricing Tiers', 'fa-tags');
        (slide.tiers || []).forEach((tier, i) => {
            html += `<div class="bg-[#0b1121] border border-slate-700/50 rounded-lg p-3 mb-3 relative group transition hover:border-slate-500">
                        <div class="absolute -top-2 -right-2 bg-slate-800 hover:bg-red-500 text-slate-400 hover:text-white w-6 h-6 rounded-full flex items-center justify-center text-[10px] cursor-pointer opacity-0 group-hover:opacity-100 transition-all border border-slate-600 shadow-md" onclick="removeArrayItem('tiers', ${i})"><i class="fa-solid fa-trash"></i></div>
                        <div class="flex items-center justify-between mb-3 border-b border-slate-800 pb-2">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Highlight this tier?</label>
                            <input type="checkbox" class="cursor-pointer" ${tier.highlight ? 'checked' : ''} onchange="updateArrayItem('tiers', ${i}, 'highlight', this.checked)">
                        </div>
                        <div class="flex gap-3 mb-3">
                            <div class="flex flex-col gap-1.5 w-1/2">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Plan Name</label>
                                <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-1.5 text-xs text-white outline-none" value="${escapeHtml(tier.name)}" oninput="updateArrayItem('tiers', ${i}, 'name', this.value)">
                            </div>
                            <div class="flex flex-col gap-1.5 w-1/2">
                                <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Price</label>
                                <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-1.5 text-xs text-white outline-none" value="${escapeHtml(tier.price)}" oninput="updateArrayItem('tiers', ${i}, 'price', this.value)">
                            </div>
                        </div>
                        <div class="flex flex-col gap-1.5">
                            <label class="text-[0.65rem] font-bold text-slate-400 uppercase tracking-widest">Main Feature</label>
                            <input type="text" class="w-full bg-[#020617] border border-slate-700 focus:border-blue-500 rounded-md px-3 py-1.5 text-xs text-white outline-none" value="${escapeHtml(tier.feature)}" oninput="updateArrayItem('tiers', ${i}, 'feature', this.value)">
                        </div>
                     </div>`;
        });
        if (reachedPricingLimit) {
            html += `<div class="mb-3 rounded-lg border border-amber-700/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-200">Pricing supports up to ${pricingLimit.limit} ${pricingLimit.label} per slide to guarantee fit on all screens. Create another ${pricingLimit.slideName} slide for additional content.</div>`;
        }
        html += `<button class="w-full border text-xs font-bold py-2.5 rounded-lg transition-colors ${reachedPricingLimit ? 'bg-slate-900 text-slate-500 border-slate-800 cursor-not-allowed' : 'bg-slate-800 hover:bg-slate-700 border-slate-700 text-white'}" onclick="addArrayItem('tiers', {name:'New Plan', price:'$99', feature:'Description', highlight:false})" ${reachedPricingLimit ? 'disabled' : ''}><i class="fa-solid fa-plus mr-2"></i>Add Tier</button>`;
        html += closeBlock;
    }
    else if (slide.type === 'custom_baked_template') {
    // The Layout Engine has already done the heavy lifting of converting 
    // fractional columns and properties into Tailwind HTML.
    slideHtml = `
        <div class="deck-preview h-full w-full relative">
            ${slide.htmlContent || ''}
        </div>
    `;
}
    else {
        html += `<div class="bg-blue-900/20 border border-blue-800 rounded-xl p-4 text-xs text-blue-200 leading-relaxed shadow-inner mb-6"><i class="fa-solid fa-wand-magic-sparkles text-blue-400 mr-2 text-lg float-left"></i> This template is fully editable directly on the slide preview.</div>`;
    }

    // SPEAKER NOTES (Always at bottom)
    html += `
        <div class="inspector-card inspector-card--notes mt-auto mb-10">
            <div class="inspector-card__header">
                <div class="flex items-center">
                <i class="fa-solid fa-clipboard-user text-purple-400 mr-2 text-xs"></i>
                <span class="text-[0.65rem] font-extrabold uppercase tracking-widest text-slate-300">Speaker Notes</span>
                </div>
                <span class="inspector-helper-label">Presenter View</span>
            </div>
            <div class="inspector-card__body pt-0">
                <p class="inspector-helper-copy mb-3">Use short cues, demo reminders, and timing checkpoints. These notes appear in the dual-window speaker view.</p>
                <textarea class="speaker-notes-input" placeholder="Type presenter notes, cues, timing reminders, or live-demo prompts here." oninput="updateSlide('notes', this.value)">${escapeHtml(slide.notes || '')}</textarea>
            </div>
        </div>
    `;

    html += presentationSettings;
    form.innerHTML = html;
}
// Add this helper function somewhere in your JS scope
function renderCustomBlockLayout(slide) {
    if (!slide.layout || !Array.isArray(slide.layout) || slide.layout.length === 0) return '<div class="text-slate-500 p-10 text-center w-full h-full flex items-center justify-center font-bold uppercase tracking-widest text-xs">Empty Custom Layout</div>';

    let html = `<div class="w-full h-full flex flex-col justify-center p-12 gap-8 z-10 relative">`;
    
    slide.layout.forEach((row, rIndex) => {
        let gridClass = 'grid-cols-1';
        if (row.layoutType === 'split-50-50') gridClass = 'grid-cols-2';
        if (row.layoutType === 'split-33-33-33') gridClass = 'grid-cols-3';

        html += `<div class="grid ${gridClass} gap-8 w-full items-center">`;
        
        row.blocks.forEach((block, bIndex) => {
            html += `<div class="flex flex-col gap-2">`;
            const editAttrs = `contenteditable="true" onblur="handleBlockEdit(event, ${rIndex}, ${bIndex}, 'content')"`;
            
            switch (block.type) {
                case 'header':
                    html += `<h2 class="text-4xl font-bold ${block.color || 'text-white'} outline-none" ${editAttrs}>${escapeHtml(block.content || 'Header Text')}</h2>`;
                    break;
                case 'text':
                    html += `<p class="text-lg ${block.color || 'text-slate-300'} leading-relaxed outline-none" ${editAttrs}>${escapeHtml(block.content || 'Enter paragraph text here...')}</p>`;
                    break;
                case 'image':
                    let imgStyle = block.content ? `background-image: url('${block.content}'); background-size: cover; background-position: center;` : `background-color: rgba(255,255,255,0.05);`;
                    html += `<div class="w-full aspect-video rounded-xl border border-slate-700/50 shadow-lg flex items-center justify-center cursor-pointer transition hover:border-blue-500" style="${imgStyle}" onclick="document.getElementById('customImgUpload_${rIndex}_${bIndex}').click()">
                                ${!block.content ? '<i class="fa-solid fa-image text-4xl text-slate-600 pointer-events-none"></i>' : ''}
                             </div>
                             <input type="file" id="customImgUpload_${rIndex}_${bIndex}" class="hidden" accept="image/*" onchange="handleBlockImage(event, ${rIndex}, ${bIndex})">`;
                    break;
                case 'metric':
                    html += `<div class="flex flex-col">
                                <div class="text-6xl font-black outline-none" style="color: var(--accent-color)" ${editAttrs}>${escapeHtml(block.content || '99%')}</div>
                                <div class="text-sm font-bold text-slate-400 uppercase tracking-widest mt-1 outline-none" contenteditable="true" onblur="handleBlockEdit(event, ${rIndex}, ${bIndex}, 'label')">${escapeHtml(block.label || 'Metric Label')}</div>
                             </div>`;
                    break;
                case 'list':
                    html += `<ul class="space-y-3">
                                <li class="flex items-start gap-3"><i class="fa-solid fa-check-circle mt-1" style="color: var(--accent-color)"></i> <span class="text-slate-300 outline-none w-full block" ${editAttrs}>${escapeHtml(block.content || 'List item')}</span></li>
                             </ul>`;
                    break;
                case 'code':
                    html += `<div class="w-full bg-slate-950 rounded-lg border border-slate-800 overflow-hidden shadow-2xl">
                                <div class="h-8 bg-slate-900 flex items-center px-4 gap-1.5 border-b border-slate-800">
                                    <div class="w-2.5 h-2.5 rounded-full bg-red-500/80"></div>
                                    <div class="w-2.5 h-2.5 rounded-full bg-yellow-500/80"></div>
                                    <div class="w-2.5 h-2.5 rounded-full bg-green-500/80"></div>
                                </div>
                                <pre class="p-4 text-sm font-mono text-emerald-400 overflow-x-auto outline-none whitespace-pre-wrap" ${editAttrs}>${escapeHtml(block.content || 'console.log("Hello World");')}</pre>
                             </div>`;
                    break;
            }
            html += `</div>`;
        });
        html += `</div>`;
    });
    
    html += `</div>`;
    return html;
}
// ---f INLINE EDITING SYNC ---
function handleInlineEditPrimitive(event, arrayName, index) {
    let val = event.target.innerText;
    const slide = slides.find(s => s.id === currentSlideId);
    if (slide && slide[arrayName]) { slide[arrayName][index] = val; saveProjects(); renderEditor(); }
}
function handleInlineEdit(event, key, arrayName = null, index = null) {
    let val = event.target.innerText;
    const slide = slides.find(s => s.id === currentSlideId);
    if (!slide) return;
    
    if (arrayName !== null && index !== null) slide[arrayName][index][key] = val;
    else slide[key] = val;
    saveProjects();

    const form = document.getElementById('editorForm');
    if (form) {
        const inputs = form.querySelectorAll('input[type="text"], textarea');
        inputs.forEach(input => {
            if (input.getAttribute('oninput') && input.getAttribute('oninput').includes(`'${key}'`)) input.value = val;
        });
    }
}
// ADD THESE NEW BLOCK EDITING HANDLERS:
window.handleBlockEdit = function(event, rIndex, bIndex, field) {
    const slide = slides.find(s => s.id === currentSlideId);
    if (slide && slide.layout && slide.layout[rIndex] && slide.layout[rIndex].blocks[bIndex]) {
        slide.layout[rIndex].blocks[bIndex][field] = event.target.innerText;
        saveProjects();
    }
};

window.handleBlockImage = function(event, rIndex, bIndex) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function (e) {
        resizeImageForStorage(e.target.result, (resizedUrl) => {
            const slide = slides.find(s => s.id === currentSlideId);
            if (slide && slide.layout && slide.layout[rIndex] && slide.layout[rIndex].blocks[bIndex]) {
                slide.layout[rIndex].blocks[bIndex].content = resizedUrl;
                saveProjects();
                renderPreview();
            }
        });
    };
    reader.readAsDataURL(file);
};

function createEditableTag(tagName, classNames, content, key, arrayName = null, index = null) {
    let args = arrayName ? `'${key}', '${arrayName}', ${index}` : `'${key}'`;
    return `<${tagName} class="${classNames}" contenteditable="true" onblur="handleInlineEdit(event, ${args})">${escapeHtml(content)}</${tagName}>`;
}
function createEditablePrimitive(tagName, classNames, content, arrayName, index) {
    return `<${tagName} class="${classNames}" contenteditable="true" onblur="handleInlineEditPrimitive(event, '${arrayName}', ${index})">${escapeHtml(content)}</${tagName}>`;
}

// --- DATA MUTATORS ---
const ARRAY_LIMITS = {
    intro: { tags: { limit: 4, label: 'tags', slideName: 'Title Hero' } },
    split: { bullets: { limit: 4, label: 'bullets', slideName: 'Side-by-Side' } },
    grid: { cards: { limit: 4, label: 'cards', slideName: 'Feature Grid' } },
    list: { items: { limit: 5, label: 'rows', slideName: 'Checklist' } },
    glass_intro: { badges: { limit: 4, label: 'badges', slideName: 'Glass Hero' } },
    comparison: {
        leftPoints: { limit: 4, label: 'left points', slideName: 'Comparison Panels' },
        rightPoints: { limit: 4, label: 'right points', slideName: 'Comparison Panels' }
    },
    showcase_window: { bullets: { limit: 4, label: 'bullets', slideName: 'Showcase Window' } },
    corp_image_text: { bullets: { limit: 4, label: 'bullets', slideName: 'Magazine Layout' } },
    corp_team: { team: { limit: 4, label: 'team members', slideName: 'Team Layout' } },
    pitch_stats: { stats: { limit: 4, label: 'metrics', slideName: 'Metrics' } },
    pitch_timeline: { timeline: { limit: 4, label: 'milestones', slideName: 'Timeline' } },
    pitch_pricing: { tiers: { limit: 4, label: 'tiers', slideName: 'Pricing' } },
    roadmap_cards: { phases: { limit: 4, label: 'stages', slideName: 'Roadmap Cards' } },
    res_title: { badges: { limit: 4, label: 'badges', slideName: 'Research Title' } },
    res_abstract: { bullets: { limit: 4, label: 'bullets', slideName: 'Abstract' } },
    res_methodology: { phases: { limit: 4, label: 'stages', slideName: 'Methodology' } },
    res_data: { stats: { limit: 4, label: 'metrics', slideName: 'Data Insight' } },
    res_citations: { items: { limit: 10, label: 'citations', slideName: 'Citations' } },
    res_lit_review: { themes: { limit: 4, label: 'themes', slideName: 'Literature Review' } },
    res_variables: { hypotheses: { limit: 3, label: 'hypotheses', slideName: 'Hypothesis' } },
    res_limits: { points: { limit: 5, label: 'points', slideName: 'Limitations' } },
    res_collaborators: { logos: { limit: 6, label: 'partners', slideName: 'Funding' } },
    res_key_takeaway: { points: { limit: 3, label: 'summary points', slideName: 'Takeaway' } }
};

function getArrayLimit(slide, arrayName) {
    if (!slide) return null;
    return ARRAY_LIMITS[slide.type]?.[arrayName] || null;
}

function getSlideCapacityHint(slide) {
    if (!slide) return '';
    const limits = ARRAY_LIMITS[slide.type];
    if (!limits) return '';

    const parts = Object.entries(limits).map(([arrayName, config]) => {
        const currentCount = Array.isArray(slide[arrayName]) ? slide[arrayName].length : 0;
        return `${currentCount}/${config.limit} ${config.label}`;
    });

    return parts.length ? `Capacity: ${parts.join(' • ')}` : '';
}

function updateSlide(key, value) { const slide = slides.find(s => s.id === currentSlideId); if (slide) { slide[key] = value; renderSlideList(); renderPreview(); saveProjects(); } }
function updateArrayItem(arrayName, index, key, value) { const slide = slides.find(s => s.id === currentSlideId); if (slide && slide[arrayName]) { slide[arrayName][index][key] = value; renderPreview(); saveProjects(); } }
function updateArrayPrimitive(arrayName, index, value) { const slide = slides.find(s => s.id === currentSlideId); if (slide && slide[arrayName]) { slide[arrayName][index] = value; renderPreview(); saveProjects(); } }
function addArrayItem(arrayName, defaultObj) {
    const slide = slides.find(s => s.id === currentSlideId);
    if (!slide) return;
    if (!slide[arrayName]) slide[arrayName] = [];

    const limitConfig = getArrayLimit(slide, arrayName);
    if (limitConfig && slide[arrayName].length >= limitConfig.limit) {
        alert(`This slide supports up to ${limitConfig.limit} ${limitConfig.label}. Add another ${limitConfig.slideName} slide for additional content.`);
        return;
    }

    slide[arrayName].push(defaultObj);
    renderEditor();
    renderPreview();
    saveProjects();
}
function addArrayPrimitive(arrayName, defaultStr) {
    const slide = slides.find(s => s.id === currentSlideId);
    if (!slide) return;
    if (!slide[arrayName]) slide[arrayName] = [];

    const limitConfig = getArrayLimit(slide, arrayName);
    if (limitConfig && slide[arrayName].length >= limitConfig.limit) {
        alert(`This slide supports up to ${limitConfig.limit} ${limitConfig.label}. Add another ${limitConfig.slideName} slide for additional content.`);
        return;
    }

    slide[arrayName].push(defaultStr);
    renderEditor();
    renderPreview();
    saveProjects();
}
function removeArrayItem(arrayName, index) { const slide = slides.find(s => s.id === currentSlideId); if (slide && slide[arrayName]) { slide[arrayName].splice(index, 1); renderEditor(); renderPreview(); saveProjects(); } }
function removeArrayPrimitive(arrayName, index) { removeArrayItem(arrayName, index); }
function removeImage(key, arrayName = null, index = null) {
    if (arrayName !== null && index !== null) updateArrayItem(arrayName, index, key, '');
    else updateSlide(key, '');
    renderEditor(); saveProjects();
}

function addSlide(type, templateId = null) {
    slideCounter++;
    let newSlide = { id: 'slide_' + Date.now() + '_' + Math.floor(Math.random() * 1000), type: type, navName: 'New Slide', title: 'Main Topic Heading', transition: 'fade-in', bgOverride: 'bg-default', notes: '' };

    // ADD THIS NEW BLOCK:
    if (type === 'custom_block_layout') {
        newSlide.kicker = 'Custom Layout';
        if (templateId) {
            let templates = window.customTemplates || JSON.parse(localStorage.getItem('openDeckCustomTemplates') || '[]');
            let t = templates.find(x => x.id === templateId);
            if (t && t.layout) {
                newSlide.navName = t.name || 'Custom Layout';
                newSlide.layout = JSON.parse(JSON.stringify(t.layout)); // Deep copy the grid
            }
        } else {
            newSlide.layout = [];
        }
    }
    // Enhanced "Empty State" text for existing templates
    if (type === 'intro') { newSlide.subtitle = 'Type your subtitle or presenter name here'; newSlide.icon = 'fa-desktop'; newSlide.tags = []; }
    if (type === 'split') { newSlide.subtitle = 'Explain the core details of this concept in a few sentences here. Click to edit.'; newSlide.bullets = ['Type your first supporting point here', 'Add a secondary detail here']; newSlide.boxTitle = 'Key Takeaway'; newSlide.boxText = 'Summarize the most important metric or outcome here.'; newSlide.boxIcon = 'fa-lightbulb'; }
    if (type === 'grid') { newSlide.subtitle = 'Break down your topic into key areas or features.'; newSlide.cards = [{ title: 'First Feature', text: 'Describe the value proposition here.', icon: 'fa-cube', color: '#3B82F6' }, { title: 'Second Feature', text: 'Highlight a technical advantage here.', icon: 'fa-bolt', color: '#10B981' }]; }
    if (type === 'list') { newSlide.subtitle = 'List out requirements, status flags, or compliance steps.'; newSlide.items = [{ label: 'Define project scope', value: 'DONE', icon: 'fa-check-circle', color: '#10B981' }]; }
    if (type === 'code') { newSlide.subtitle = 'Explain what this code block or configuration does.'; newSlide.codeHeader = 'setup.sh'; newSlide.codeContent = '#!/bin/bash\necho "Start typing your code here"'; newSlide.codeColor = 'text-green-400'; }
    if (type === 'cta') { newSlide.subtitle = 'Tell the audience what action they should take next.'; newSlide.icon = 'fa-rocket'; newSlide.link = 'go.company.com/action'; }
    if (type === 'glass_intro') { newSlide.kicker = 'Presentation System'; newSlide.subtitle = 'Craft a high-polish narrative with cinematic typography, glass surfaces, and crisp supporting badges.'; newSlide.icon = 'fa-wand-magic-sparkles'; newSlide.badges = [{ text: 'Auditable', icon: 'fa-shield-halved', color: '#10B981' }, { text: 'Modular', icon: 'fa-layer-group', color: '#3B82F6' }, { text: 'Presentation-Ready', icon: 'fa-rocket', color: '#F97316' }]; newSlide.bgOverride = 'bg-aurora'; }
    if (type === 'comparison') { newSlide.kicker = 'Decision Frame'; newSlide.subtitle = 'Use this layout for before-and-after thinking, tradeoffs, or competing approaches.'; newSlide.leftTitle = 'Current Workflow'; newSlide.leftText = 'Explain the friction, risk, or manual overhead on this side.'; newSlide.leftIcon = 'fa-triangle-exclamation'; newSlide.leftPoints = ['Fragmented steps across tools', 'Low visibility during review', 'Difficult to keep presentation quality consistent']; newSlide.rightTitle = 'OpenDeck Workflow'; newSlide.rightText = 'Describe the cleaner, more scalable path on this side.'; newSlide.rightIcon = 'fa-rocket'; newSlide.rightPoints = ['Reusable templates for common stories', 'Inline editing keeps iteration fast', 'Higher polish without extra design work']; newSlide.bgOverride = 'bg-aurora'; }
    if (type === 'showcase_window') { newSlide.kicker = 'Showcase'; newSlide.subtitle = 'Combine narrative context with a polished macOS-style window for code, repo structure, or walkthrough content.'; newSlide.bullets = ['Introduce the scenario with one framing sentence', 'Use the window to display structure, config, or terminal output', 'Pair the visual with clear takeaways on the left']; newSlide.windowTitle = 'slides/launch-plan.yaml'; newSlide.codeHeader = 'Presentation Frame'; newSlide.codeContent = 'name: keynote_launch\nstyle: aurora_glass\nmode: live_preview\npolish:\n  - cinematic_intro\n  - comparison_panels\n  - roadmap_cards'; newSlide.codeColor = 'text-blue-400'; newSlide.bgOverride = 'bg-deepblue'; }
    if (type === 'roadmap_cards') { newSlide.kicker = 'Roadmap'; newSlide.subtitle = 'Map the rollout in stages with clear ownership and visual status markers.'; newSlide.phases = [{ title: 'Foundation', text: 'Expand the template library with stronger narrative-building blocks.', status: 'Current', color: '#3B82F6' }, { title: 'Polish', text: 'Refine motion, backgrounds, and visual systems across exported decks.', status: 'Next', color: '#8B5CF6' }, { title: 'Advanced', text: 'Introduce more presentational controls for high-stakes talks and demos.', status: 'Later', color: '#F97316' }]; newSlide.bgOverride = 'bg-sunset'; }

    // Enhanced Empty States & NEW TEMPLATES
    if (type === 'corp_title') { newSlide.subtitle = 'Department or presentation context'; newSlide.author = 'Presenter Name'; newSlide.bgOverride = 'bg-purewhite'; }
    if (type === 'corp_quote') { newSlide.title = 'Type a powerful customer quote or visionary statement here.'; newSlide.author = 'Client Name / Role'; }
    if (type === 'corp_image_text') { newSlide.subtitle = 'Type a detailed editorial description here to support the visual.'; newSlide.bullets = ['First supporting detail', 'Second supporting detail']; }

    // NEW: Title & Content, Team, Pricing
    if (type === 'corp_basic') { newSlide.subtitle = 'Type your comprehensive slide content here. This free-form area is perfect for paragraphs, meeting notes, or extended thoughts.'; newSlide.bgOverride = 'bg-purewhite'; }
    if (type === 'corp_team') { newSlide.subtitle = 'The minds behind the magic.'; newSlide.team = [{ name: 'Jane Doe', role: 'CEO & Founder', image: '' }, { name: 'John Smith', role: 'Lead Developer', image: '' }, { name: 'Alice Jones', role: 'Designer', image: '' }]; }
    if (type === 'pitch_pricing') { newSlide.subtitle = 'Choose the plan that fits your needs.'; newSlide.tiers = [{ name: 'Starter', price: 'Free', feature: 'Basic features', highlight: false }, { name: 'Pro', price: '$29', feature: 'All premium features', highlight: true }, { name: 'Enterprise', price: 'Custom', feature: 'Dedicated support', highlight: false }]; }

    if (type === 'pitch_hero') { newSlide.kicker = 'Creative Pitch'; newSlide.subtitle = 'State your big visionary idea here.'; }
    if (type === 'pitch_stats') { newSlide.subtitle = 'Highlight your key performance indicators.'; newSlide.stats = [{ value: '99%', label: 'Uptime', color: '#3B82F6' }, { value: '10x', label: 'Growth', color: '#10B981' }]; }
    if (type === 'pitch_timeline') { newSlide.subtitle = 'Showcase your roadmap or history.'; newSlide.timeline = [{ year: '2025', text: 'Launch Phase', color: '#3B82F6' }, { year: '2026', text: 'Scale Operations', color: '#3B82F6' }]; }
    if (type === 'res_title') { 
        newSlide.title = 'Project Title'; newSlide.subtitle = 'University or Department Name'; 
        newSlide.kicker = 'Research Paper Presentation'; newSlide.icon = 'fa-microscope'; 
        newSlide.badges = [{text: 'May 2026', icon: 'fa-calendar', color: '#3B82F6'}];
    }
    if (type === 'res_abstract') { 
        newSlide.title = 'Abstract & Scope'; newSlide.subtitle = 'Define the problem statement and research questions.'; 
        newSlide.bullets = ['Hypothesis A', 'Expected Outcome']; 
        newSlide.boxTitle = 'Contribution'; newSlide.boxText = 'Summarize the unique value of this study.'; 
        newSlide.boxIcon = 'fa-book-open'; 
    }
    if (type === 'res_methodology') { 
        newSlide.title = 'Research Design'; newSlide.phases = [
            {title: 'Data Collection', text: 'Surveying 500 participants.', status: 'Done', color: '#10B981'},
            {title: 'Analysis', text: 'Running regression models.', status: 'Active', color: '#3B82F6'}
        ]; 
    }
    if (type === 'res_data') { 
        newSlide.title = 'Quantitative Analysis'; newSlide.stats = [
            {value: 'p < 0.05', label: 'Significance', color: '#3B82F6'},
            {value: 'N=1200', label: 'Sample Size', color: '#10B981'}
        ]; 
    }
    if (type === 'res_citations') { 
        newSlide.title = 'Selected Bibliography'; newSlide.items = [
            {label: 'Rawls, J. (1971). A Theory of Justice.', value: 'PRIMARY', icon: 'fa-quote-right', color: '#3B82F6'}
        ]; 
    }
    if (type === 'res_lit_review') {
        newSlide.title = 'Current State of Research';
        newSlide.subtitle = 'Thematic review of contemporary literature.';
        newSlide.themes = [
            { title: 'Theoretic Framework', text: 'Core principles of justice.', icon: 'fa-scale-balanced' },
            { title: 'Practical Application', text: 'How it works in law.', icon: 'fa-gavel' }
        ];
    }
    if (type === 'res_variables') {
        newSlide.title = 'Theoretical Model';
        newSlide.ivTitle = 'Independent Variables';
        newSlide.dvTitle = 'Dependent Variables';
        newSlide.hypotheses = ['Higher IV correlates to increased DV outcome.'];
    }
    if (type === 'res_limits') {
        newSlide.title = 'Critical Limitations';
        newSlide.subtitle = 'Addressing the constraints of this study.';
        newSlide.points = [
            { label: 'LIMITATION', text: 'Small sample size (N=45).', color: '#EF4444', icon: 'fa-circle-exclamation' },
            { label: 'FUTURE WORK', text: 'Longitudinal study over 10 years.', color: '#3B82F6', icon: 'fa-arrow-right' }
        ];
    }
    if (type === 'res_collaborators') {
        newSlide.title = 'Institutional Support';
        newSlide.logos = [
            { name: 'University of Hawaii', role: 'Primary Research', icon: 'fa-building-columns' },
            { name: 'Grant Agency', role: 'Funding Body', icon: 'fa-hand-holding-dollar' }
        ];
    }
    if (type === 'res_key_takeaway') {
        newSlide.title = 'The Bottom Line';
        newSlide.subtitle = 'What this means for the field.';
        newSlide.impact = 'Justice is achieved only when the rules are transparent.';
        newSlide.icon = 'fa-lightbulb';
    }
    slides.push(newSlide);
    currentSlideId = newSlide.id;
    hideModal('templateModal');
    saveProjects();
    renderApp();
    setTimeout(() => { document.getElementById('slideList').scrollTop = 9999; }, 50);
}

function duplicateSlide(id, event) {
    event.stopPropagation();
    const index = slides.findIndex(s => s.id === id);
    if (index === -1) return;
    slideCounter++;
    const cloned = JSON.parse(JSON.stringify(slides[index]));
    cloned.id = 'slide_' + Date.now() + '_' + Math.floor(Math.random() * 1000);
    cloned.navName = cloned.navName + ' (Copy)';
    slides.splice(index + 1, 0, cloned);
    currentSlideId = cloned.id;
    saveProjects();
    renderApp();
}

function deleteSlide(id, event) {
    event.stopPropagation();
    if (slides.length <= 1) return alert("You must have at least one slide.");
    if (confirm("Delete this slide?")) {
        slides = slides.filter(s => s.id !== id);
        if (currentSlideId === id) currentSlideId = slides[0].id;
        saveProjects();
        renderApp();
    }
}

// --- 🔥 FULL HTML GENERATORS ---
function generateSlideHTML(slide, isExport = false) {
    let animClass = slide.transition || 'fade-in';
    if (!isExport) animClass = 'fade-in';

    let html = '';

    if (slide.type === 'intro' || slide.type === 'res_title') {
        let tagsHtml = (slide.tags || []).map((t, i) => `<span class="flex items-center"><i class="fa-solid ${escapeHtml(t.icon)} mr-2" style="color: ${t.color || 'var(--accent-color)'}"></i>${createEditableTag('span', '', t.text, 'text', 'tags', i)}</span>`).join('');
        html = `
            <div class="od-deck-shell text-center ${animClass}">
                <div class="od-title-mark mx-auto"></div>
                <div class="mb-6 inline-flex items-center justify-center w-24 h-24 rounded-[1.75rem] border border-white/10 bg-white/5 shadow-[0_0_35px_-12px_var(--accent-color)] overflow-hidden p-4">
    ${renderIconOrImage(slide.icon, "text-4xl accent-text drop-shadow-[0_0_18px_var(--accent-color)]")}
</div>
                ${createEditableTag('h1', 'text-7xl font-black tracking-[-0.06em] mb-4 leading-[0.95] w-full text-white', slide.title, 'title')}
                ${createEditableTag('p', 'text-2xl text-slate-300 uppercase tracking-[0.22em] font-light mb-12 w-full block', slide.subtitle, 'subtitle')}
                <div class="od-badge-row justify-center text-sm font-mono">${tagsHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'split' || slide.type === 'res_abstract') {
        let bulletsHtml = (slide.bullets || []).map((b, i) => `<li class="flex items-start w-full"><i class="fa-solid fa-angle-right accent-text mr-3 mt-1.5"></i> ${createEditablePrimitive('span', 'flex-grow w-full block', b, 'bullets', i)}</li>`).join('');
        let rightHtml = slide.image ? `<img src="${slide.image}" class="w-full h-80 object-cover rounded-2xl border border-slate-700 shadow-2xl">` : `
            <div class="od-card h-full flex flex-col justify-center items-center text-center w-full" style="--card-accent: var(--accent-color)">
                <div class="od-card__icon"><i class="fa-solid ${escapeHtml(slide.boxIcon)} text-3xl text-slate-400"></i></div>
                ${createEditableTag('h3', 'text-2xl font-bold text-white mb-3 w-full', slide.boxTitle, 'boxTitle')}
                ${createEditableTag('p', 'text-slate-400 leading-relaxed w-full', slide.boxText, 'boxText')}
            </div>`;

        html = `
            <div class="od-deck-shell ${animClass}">
                <div class="od-title-mark"></div>
                ${createEditableTag('h2', 'text-5xl font-extrabold tracking-tight mb-8 w-full text-white', slide.title, 'title')}
                <div class="od-panel-grid items-center">
                    <div class="space-y-8 w-full">
                        ${createEditableTag('p', 'od-lead w-full block', slide.subtitle, 'subtitle')}
                        <ul class="space-y-5 text-slate-400 text-lg w-full">${bulletsHtml}</ul>
                    </div>
                    <div class="h-full flex flex-col justify-center w-full">${rightHtml}</div>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'grid') {
        const isQuadGrid = slide.cards && slide.cards.length === 4;
        let colsClass = isQuadGrid ? 'grid-cols-2 od-grid-quad' : 'grid-cols-3';
        let cardsHtml = (slide.cards || []).map((c, i) => `
            <div class="od-card text-center w-full ${isQuadGrid ? 'od-card--compact' : ''}" style="--card-accent:${c.color || 'var(--accent-color)'}">
                ${c.image ? `<img src="${c.image}" class="h-16 w-16 mx-auto object-cover rounded-2xl mb-6">` : `<div class="od-card__icon mx-auto"><i class="fa-solid ${escapeHtml(c.icon)} text-2xl" style="color: ${c.color || 'var(--accent-color)'}"></i></div>`}
                ${createEditableTag('h4', 'text-xl font-bold text-white mb-3 w-full block', c.title, 'title', 'cards', i)}
                ${createEditableTag('p', 'text-sm text-slate-400 leading-relaxed w-full block', c.text, 'text', 'cards', i)}
            </div>
        `).join('');
        html = `
            <div class="od-deck-shell ${animClass}">
                <div class="od-title-mark mx-auto"></div>
                ${createEditableTag('h2', `${isQuadGrid ? 'text-4xl mb-3' : 'text-5xl mb-4'} font-extrabold w-full text-center text-white tracking-tight`, slide.title, 'title')}
                ${createEditableTag('p', `od-lead ${isQuadGrid ? 'mb-6' : 'mb-10'} w-full text-center block`, slide.subtitle, 'subtitle')}
                <div class="grid ${colsClass} ${isQuadGrid ? 'gap-5' : 'gap-8'} w-full">${cardsHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'list' || slide.type === 'res_citations') {
        let itemsHtml = (slide.items || []).map((item, i) => `
            <li class="od-check-row w-full">
                <div class="flex items-center w-full"><i class="fa-solid ${escapeHtml(item.icon)} mr-4 text-xl shrink-0" style="color: ${item.color || 'var(--accent-color)'}"></i>${createEditableTag('span', 'text-lg font-semibold w-full block', item.label, 'label', 'items', i)}</div>
                <div class="px-3 py-1 rounded border shadow-inner shrink-0" style="border-color: color-mix(in srgb, ${item.color || 'var(--accent-color)'} 40%, transparent); background: color-mix(in srgb, ${item.color || 'var(--accent-color)'} 10%, transparent); color: ${item.color || 'var(--accent-color)'};">
                    ${createEditableTag('span', 'font-bold uppercase tracking-widest text-xs drop-shadow-md', item.value, 'value', 'items', i)}
                </div>
            </li>
        `).join('');
        html = `
            <div class="od-deck-shell ${animClass}">
                <div class="od-title-mark"></div>
                ${createEditableTag('h2', 'text-5xl font-extrabold mb-8 w-full text-white tracking-tight', slide.title, 'title')}
                <div class="od-checklist-shell w-full">
                    <div class="space-y-6 w-full">
                        ${createEditableTag('p', 'od-lead mb-6 w-full block', slide.subtitle, 'subtitle')}
                        <ul class="space-y-3 w-full">${itemsHtml}</ul>
                    </div>
                    <div class="od-card flex flex-col items-center justify-center h-full w-full text-center" style="--card-accent: var(--accent-color)">
                        <div class="od-card__icon"><i class="fa-solid fa-list-check text-3xl accent-text"></i></div>
                        <h3 class="text-3xl font-bold text-white tracking-tight text-center pointer-events-none">Checklist</h3>
                        <p class="text-sm text-slate-500 mt-3 uppercase tracking-[0.22em]">Structured status tracking</p>
                    </div>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'code') {
        let codeColor = slide.codeColor || 'text-green-400';
        html = `
            <div class="od-deck-shell ${animClass}">
                <div class="od-title-mark"></div>
                ${createEditableTag('h2', 'text-5xl font-extrabold tracking-tight mb-4 w-full text-white', slide.title, 'title')}
                ${createEditableTag('p', 'od-lead mb-8 w-full block', slide.subtitle, 'subtitle')}
                <div class="od-code-shell text-left">
                    <div class="od-code-shell__head">
                        <div class="flex gap-2 mr-4 pointer-events-none"><div class="w-3 h-3 rounded-full bg-red-500"></div><div class="w-3 h-3 rounded-full bg-yellow-500"></div><div class="w-3 h-3 rounded-full bg-green-500"></div></div>
                        <i class="fa-solid fa-file-code text-slate-500 mr-2 text-xs pointer-events-none"></i>
                        ${createEditableTag('span', 'text-xs text-slate-400 font-mono tracking-widest w-full block', slide.codeHeader, 'codeHeader')}
                    </div>
                    <div class="p-6 overflow-x-auto od-window__body">
                        ${createEditableTag('pre', `font-mono text-sm ${codeColor} leading-relaxed whitespace-pre-wrap outline-none w-full block`, slide.codeContent, 'codeContent')}
                    </div>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'cta') {
        html = `
            <div class="od-deck-shell text-center ${animClass}">
                <div class="od-title-mark mx-auto"></div>
                ${slide.image ? `<img src="${slide.image}" class="h-40 mx-auto object-cover rounded-[1.75rem] mb-8 shadow-2xl border border-slate-700">` : `<div class="inline-flex items-center justify-center w-24 h-24 rounded-[1.75rem] border border-white/10 bg-white/5 mb-8"><i class="fa-solid ${escapeHtml(slide.icon)} text-4xl text-white drop-shadow-lg"></i></div>`}
                ${createEditableTag('h2', 'text-6xl font-black mb-6 tracking-[-0.05em] w-full text-white', slide.title, 'title')}
                ${createEditableTag('p', 'text-2xl text-slate-300 mb-10 max-w-3xl mx-auto font-light w-full block leading-relaxed', slide.subtitle, 'subtitle')}
                ${slide.link ? `<div class="inline-flex items-center bg-slate-950/80 border border-slate-700 px-6 py-4 rounded-full shadow-xl"><i class="fa-solid fa-link accent-text mr-3 pointer-events-none"></i>${createEditableTag('span', 'text-xl font-mono text-white font-bold', slide.link, 'link')}</div>` : ''}
            </div>
        `;
    }
    else if (slide.type === 'glass_intro') {
        let badgesHtml = (slide.badges || []).map((badge, i) => `
            <div class="od-badge">
                <i class="fa-solid ${escapeHtml(badge.icon)}" style="color: ${badge.color || 'var(--accent-color)'}"></i>
                ${createEditableTag('span', 'text-sm font-semibold', badge.text, 'text', 'badges', i)}
            </div>
        `).join('');

        html = `
            <div class="od-orb od-orb--blue"></div>
            <div class="od-orb od-orb--orange"></div>
            <div class="od-shell text-center ${animClass}">
                ${createEditableTag('div', 'od-kicker mx-auto mb-8', slide.kicker || 'Presentation System', 'kicker')}
               <div class="mb-8 inline-flex items-center justify-center w-24 h-24 rounded-[1.75rem] border border-white/10 bg-white/5 shadow-[0_0_35px_-12px_var(--accent-color)] overflow-hidden p-4">
    ${renderIconOrImage(slide.icon, "text-4xl accent-text drop-shadow-[0_0_18px_var(--accent-color)]")}
</div>
                ${createEditableTag('h1', 'text-7xl font-black tracking-[-0.06em] mb-5 leading-[0.95] w-full text-white', slide.title, 'title')}
                ${createEditableTag('p', 'text-2xl text-slate-300 font-light leading-relaxed max-w-4xl mx-auto block', slide.subtitle, 'subtitle')}
                <div class="od-badge-row justify-center">${badgesHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'comparison') {
        let leftPoints = (slide.leftPoints || []).map((point, i) => `
            <li class="od-point-item"><i class="fa-solid fa-angle-right mt-1 accent-text"></i>${createEditablePrimitive('span', 'block flex-grow', point, 'leftPoints', i)}</li>
        `).join('');
        let rightPoints = (slide.rightPoints || []).map((point, i) => `
            <li class="od-point-item"><i class="fa-solid fa-angle-right mt-1 accent-text"></i>${createEditablePrimitive('span', 'block flex-grow', point, 'rightPoints', i)}</li>
        `).join('');

        html = `
            <div class="od-shell ${animClass}">
                ${createEditableTag('div', 'od-kicker mb-6', slide.kicker || 'Decision Frame', 'kicker')}
                ${createEditableTag('h2', 'text-5xl font-extrabold tracking-tight text-white w-full', slide.title, 'title')}
                ${createEditableTag('p', 'text-xl text-slate-400 mt-4 max-w-4xl leading-relaxed block', slide.subtitle, 'subtitle')}
                <div class="od-compare-grid">
                    <div class="od-compare-panel">
                        <div class="od-compare-icon"><i class="fa-solid ${escapeHtml(slide.leftIcon)} text-2xl text-red-400"></i></div>
                        ${createEditableTag('h3', 'text-2xl font-bold text-white w-full', slide.leftTitle, 'leftTitle')}
                        ${createEditableTag('p', 'text-slate-400 mt-3 leading-relaxed block', slide.leftText, 'leftText')}
                        <ul class="od-point-list">${leftPoints}</ul>
                    </div>
                    <div class="od-compare-panel">
                        <div class="od-compare-icon"><i class="fa-solid ${escapeHtml(slide.rightIcon)} text-2xl text-emerald-400"></i></div>
                        ${createEditableTag('h3', 'text-2xl font-bold text-white w-full', slide.rightTitle, 'rightTitle')}
                        ${createEditableTag('p', 'text-slate-400 mt-3 leading-relaxed block', slide.rightText, 'rightText')}
                        <ul class="od-point-list">${rightPoints}</ul>
                    </div>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'showcase_window') {
        let bulletsHtml = (slide.bullets || []).map((bullet, i) => `
            <li class="od-point-item"><div class="w-2.5 h-2.5 rounded-full mt-1.5 shrink-0" style="background:${i === 0 ? 'var(--accent-color)' : '#64748b'}"></div>${createEditablePrimitive('span', 'block flex-grow', bullet, 'bullets', i)}</li>
        `).join('');
        let codeColor = slide.codeColor || 'text-blue-400';

        html = `
            <div class="od-shell ${animClass}">
                <div class="od-showcase-grid">
                    <div class="flex flex-col justify-center">
                        ${createEditableTag('div', 'od-kicker mb-6', slide.kicker || 'Showcase', 'kicker')}
                        ${createEditableTag('h2', 'text-5xl font-extrabold tracking-tight text-white leading-tight w-full', slide.title, 'title')}
                        ${createEditableTag('p', 'text-xl text-slate-400 mt-5 leading-relaxed block', slide.subtitle, 'subtitle')}
                        <ul class="od-point-list mt-8">${bulletsHtml}</ul>
                    </div>
                    <div class="od-window">
                        <div class="od-window__header">
                            <div class="od-window__dots">
                                <div class="od-window__dot" style="background:#ff5f56"></div>
                                <div class="od-window__dot" style="background:#ffbd2e"></div>
                                <div class="od-window__dot" style="background:#27c93f"></div>
                            </div>
                            ${createEditableTag('span', 'text-xs text-slate-400 font-mono tracking-[0.18em] uppercase block', slide.windowTitle || 'slides/launch-plan.yaml', 'windowTitle')}
                        </div>
                        <div class="od-window__body">
                            ${createEditableTag('div', 'text-[0.7rem] font-bold uppercase tracking-[0.18em] text-slate-500 mb-4 block', slide.codeHeader || 'Presentation Frame', 'codeHeader')}
                            ${createEditableTag('pre', `font-mono text-sm ${codeColor} leading-7 whitespace-pre-wrap w-full block`, slide.codeContent, 'codeContent')}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'roadmap_cards'|| slide.type === 'res_methodology') {
        const roadmapDensity = getDensityClasses((slide.phases || []).length);
        let phasesHtml = (slide.phases || []).map((phase, i) => `
            <div class="od-roadmap-card" style="--phase-color:${phase.color || 'var(--accent-color)'}">
                <div class="od-status-pill" style="color:${phase.color || 'var(--accent-color)'}; width:max-content;">
                    <i class="fa-solid fa-circle text-[0.45rem]"></i>
                    ${createEditableTag('span', 'text-[0.68rem] font-extrabold', phase.status, 'status', 'phases', i)}
                </div>
                ${createEditableTag('h3', 'text-3xl font-black tracking-tight text-white mt-6 block w-full', phase.title, 'title', 'phases', i)}
                ${createEditableTag('p', 'text-slate-400 mt-4 leading-relaxed block w-full', phase.text, 'text', 'phases', i)}
            </div>
        `).join('');

        html = `
            <div class="od-shell ${animClass} ${roadmapDensity.shell}">
                ${createEditableTag('div', 'od-kicker mb-6', slide.kicker || 'Roadmap', 'kicker')}
                ${createEditableTag('h2', 'text-5xl font-extrabold tracking-tight text-white w-full', slide.title, 'title')}
                ${createEditableTag('p', 'text-xl text-slate-400 mt-4 max-w-4xl leading-relaxed block', slide.subtitle, 'subtitle')}
                <div class="od-roadmap-grid ${roadmapDensity.grid}">${phasesHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'pitch_stats' || slide.type === 'res_data') {
        const statsDensity = getDensityClasses((slide.stats || []).length);
        const statCount = (slide.stats || []).length;
        let metricLayoutClass = '';
        if (statCount === 1) metricLayoutClass = 'od-metric-grid--single';
        else if (statCount === 2) metricLayoutClass = 'od-metric-grid--two';
        else if (statCount === 3) metricLayoutClass = 'od-metric-grid--three';
        let statsHtml = (slide.stats || []).map((stat, i) => `
            <div class="od-metric-card">
                <h3 class="text-7xl font-black mb-2 drop-shadow-lg w-full text-center outline-none" contenteditable="true" onblur="handleInlineEdit(event, 'value', 'stats', ${i})" style="color: ${stat.color || 'var(--accent-color)'}">${escapeHtml(stat.value)}</h3>
                ${createEditableTag('p', 'text-xl text-slate-400 uppercase tracking-widest font-bold w-full text-center block', stat.label, 'label', 'stats', i)}
            </div>
        `).join('');

        html = `
            <div class="od-deck-shell ${animClass} ${statsDensity.shell}">
                <div class="od-title-mark mx-auto"></div>
                ${createEditableTag('h2', 'text-5xl font-extrabold mb-4 w-full text-center text-white tracking-tight', slide.title, 'title')}
                ${createEditableTag('p', 'od-lead mb-14 w-full text-center block', slide.subtitle, 'subtitle')}
                <div class="od-metric-grid ${metricLayoutClass} ${statsDensity.grid}">${statsHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'corp_title') {
        let textColor = slide.bgOverride === 'bg-purewhite' ? 'text-black' : 'text-white';
        let authorColor = slide.bgOverride === 'bg-purewhite' ? 'text-slate-600' : 'text-slate-300';
        html = `
            <div class="od-deck-shell ${animClass} flex flex-col justify-center items-start text-left">
                <div class="od-title-mark"></div>
                ${createEditableTag('h1', `text-7xl font-black mb-6 tracking-tight w-full leading-tight ${textColor}`, slide.title, 'title')}
                ${createEditableTag('p', `text-3xl font-light mb-16 w-full block ${authorColor} leading-relaxed`, slide.subtitle, 'subtitle')}
                <div class="flex items-center gap-6 mt-auto border-t border-slate-800/50 pt-8 w-full">
                    <div class="w-14 h-14 rounded-full bg-slate-800/80 border border-slate-700 flex items-center justify-center shadow-lg"><i class="fa-solid fa-user accent-text text-xl"></i></div>
                    ${createEditableTag('span', `text-xl font-semibold tracking-wide w-full block ${textColor}`, slide.author || 'Presenter Name', 'author')}
                </div>
            </div>
        `;
    }
    else if (slide.type === 'corp_quote') {
        let textColor = slide.bgOverride === 'bg-purewhite' ? 'text-black' : 'text-white';
        html = `
            <div class="od-deck-shell w-full text-center ${animClass} flex flex-col items-center justify-center">
                <i class="fa-solid fa-quote-left text-8xl mb-12 opacity-50 drop-shadow-lg" style="color:var(--accent-color)"></i>
                ${createEditableTag('h2', `text-5xl md:text-6xl font-serif italic mb-16 leading-relaxed w-full block drop-shadow ${textColor}`, slide.title, 'title')}
                <div class="flex items-center justify-center gap-6 w-full">
                    <div class="w-24 h-px bg-slate-600"></div>
                    ${createEditableTag('p', `text-xl uppercase tracking-widest font-bold ${textColor}`, slide.author || 'Author Name', 'author')}
                    <div class="w-24 h-px bg-slate-600"></div>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'corp_image_text') {
        let bulletsHtml = (slide.bullets || []).map((b, i) => `<li class="flex items-start w-full"><div class="w-3 h-3 mt-2.5 mr-5 rounded-full shrink-0 shadow-[0_0_10px_var(--accent-color)]" style="background:var(--accent-color)"></div> ${createEditablePrimitive('span', 'flex-grow w-full block text-slate-300', b, 'bullets', i)}</li>`).join('');
        let leftHtml = slide.image ? `<img src="${slide.image}" class="w-full h-full object-cover">` : `<div class="w-full h-full flex flex-col items-center justify-center text-slate-600 bg-slate-900"><i class="fa-solid fa-image text-8xl mb-4"></i><span class="text-sm font-bold uppercase tracking-widest">Image Placeholder</span></div>`;
        html = `
            <div class="od-deck-shell p-0 overflow-hidden flex h-[650px] max-w-6xl ${animClass}">
                <div class="w-1/2 h-full relative">
                    ${leftHtml}
                    <div class="absolute inset-0 bg-gradient-to-r from-transparent to-[#0b1121]"></div>
                </div>
                <div class="w-1/2 h-full flex flex-col justify-center p-16 bg-[#0b1121]">
                    ${createEditableTag('h2', 'text-5xl font-extrabold mb-6 w-full text-white tracking-tight', slide.title, 'title')}
                    ${createEditableTag('p', 'text-xl text-slate-400 leading-relaxed mb-10 w-full block font-light', slide.subtitle, 'subtitle')}
                    <ul class="space-y-6 text-lg w-full">${bulletsHtml}</ul>
                </div>
            </div>
        `;
    }
    else if (slide.type === 'pitch_hero') {
        let bgStyle = slide.image ? `background-image: url(${slide.image}); background-size: cover; background-position: center;` : `background: radial-gradient(circle at 50% 50%, var(--accent-color) 0%, #000000 100%);`;
        html = `
            <div class="od-hero-cover ${animClass}" style="${bgStyle}"></div>
            <div class="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.08),transparent_34%)] z-0"></div>
            <div class="relative z-10 w-full max-w-6xl text-center ${animClass} flex flex-col items-center justify-center h-full px-12">
                ${createEditableTag('div', 'od-kicker mb-6', slide.kicker || 'Creative Pitch', 'kicker')}
                ${createEditableTag('h1', 'text-7xl md:text-[7rem] font-black mb-8 tracking-[-0.06em] w-full drop-shadow-2xl uppercase text-white leading-none', slide.title, 'title')}
                ${createEditableTag('p', 'text-3xl text-slate-200 font-light w-full block drop-shadow-lg max-w-4xl mx-auto leading-relaxed', slide.subtitle, 'subtitle')}
            </div>
        `;
    }
    else if (slide.type === 'pitch_timeline') {
        const timelineDensity = getDensityClasses((slide.timeline || []).length);
        let timeHtml = (slide.timeline || []).map((item, i) => `
            <div class="od-timeline-item flex flex-col items-center relative z-10 group">
                <div class="w-10 h-10 rounded-full border-[6px] border-[#020617] mb-6 shadow-[0_0_20px_var(--accent-color)] transition-transform duration-300 group-hover:scale-125" style="background:${item.color || 'var(--accent-color)'}"></div>
                ${createEditableTag('h4', 'text-3xl font-black text-white mb-3 w-full text-center block drop-shadow-md tracking-tight', item.year, 'year', 'timeline', i)}
                ${createEditableTag('p', 'text-base text-slate-400 w-full text-center block leading-relaxed font-medium', item.text, 'text', 'timeline', i)}
            </div>
        `).join('');
        html = `
            <div class="od-deck-shell ${animClass} ${timelineDensity.shell} flex flex-col justify-center">
                <div class="od-title-mark mx-auto"></div>
                ${createEditableTag('h2', 'text-6xl font-extrabold mb-6 w-full text-center tracking-tight text-white', slide.title, 'title')}
                ${createEditableTag('p', 'od-lead mb-20 w-full text-center block', slide.subtitle, 'subtitle')}
                <div class="od-timeline-track ${timelineDensity.grid}">${timeHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'corp_basic') {
        let textColor = slide.bgOverride === 'bg-purewhite' ? 'text-black' : 'text-white';
        let bodyColor = slide.bgOverride === 'bg-purewhite' ? 'text-slate-600' : 'text-slate-300';
        html = `
            <div class="od-deck-shell ${animClass} flex flex-col justify-start items-start text-left py-10">
                <div class="od-title-mark"></div>
                ${createEditableTag('h2', `text-5xl font-black mb-10 tracking-tight w-full leading-tight ${textColor}`, slide.title, 'title')}
                ${createEditableTag('p', `text-2xl font-light w-full block leading-relaxed ${bodyColor}`, slide.subtitle, 'subtitle')}
            </div>
        `;
    }
    else if (slide.type === 'corp_team') {
        const teamDensity = getDensityClasses((slide.team || []).length);
        let teamHtml = (slide.team || []).map((member, i) => `
            <div class="od-profile-card flex flex-col items-center group">
                <div class="od-profile-avatar-wrap">
                    ${member.image ? `<img src="${member.image}" class="od-profile-avatar od-profile-avatar--image" style="border-color: var(--accent-color)">` : `<div class="od-profile-avatar" style="border-color: var(--accent-color)"><i class="fa-solid fa-user text-5xl text-slate-500"></i></div>`}
                </div>
                ${createEditableTag('h4', 'text-2xl font-bold text-white mb-2 w-full text-center block tracking-tight', member.name, 'name', 'team', i)}
                ${createEditableTag('p', 'text-xs text-slate-400 uppercase tracking-[0.32em] font-semibold w-full text-center block', member.role, 'role', 'team', i)}
                <div class="od-profile-accent"></div>
            </div>
        `).join('');
        html = `
            <div class="od-deck-shell ${animClass} ${teamDensity.shell} flex flex-col justify-center">
                <div class="od-title-mark mx-auto"></div>
                ${createEditableTag('h2', 'text-5xl font-bold mb-4 w-full text-center text-white tracking-tight', slide.title, 'title')}
                ${createEditableTag('p', 'od-lead mb-12 w-full text-center block', slide.subtitle, 'subtitle')}
                <div class="od-profile-grid ${teamDensity.grid}">${teamHtml}</div>
            </div>
        `;
    }
    else if (slide.type === 'pitch_pricing') {
        const pricingDensity = getDensityClasses((slide.tiers || []).length);
        let tiersHtml = (slide.tiers || []).map((tier, i) => {
            let isHigh = tier.highlight;
            return `
                <div class="od-pricing-card ${isHigh ? 'od-pricing-card--featured' : ''} flex flex-col items-center text-center" style="border-color: ${isHigh ? 'var(--accent-color)' : '#334155'}">
                    ${isHigh ? `<div class="bg-blue-600 text-white text-[10px] font-bold uppercase tracking-widest px-3 py-1 rounded-full mb-4 -mt-12 shadow-md" style="background:var(--accent-color)">Most Popular</div>` : ''}
                    ${createEditableTag('h4', 'text-2xl font-bold text-white mb-2 w-full block', tier.name, 'name', 'tiers', i)}
                    ${createEditableTag('div', 'text-5xl font-black text-white mb-6 w-full block tracking-tight', tier.price, 'price', 'tiers', i)}
                    <div class="h-px w-full bg-slate-700 mb-6"></div>
                    ${createEditableTag('p', 'text-slate-400 mb-8 w-full block leading-relaxed', tier.feature, 'feature', 'tiers', i)}
                    <div class="w-full py-3 rounded-lg font-bold text-sm transition-colors border pointer-events-none" style="${isHigh ? 'background:var(--accent-color); color:white; border-color:var(--accent-color);' : 'background:transparent; color:white; border-color:#475569;'}">Get Started</div>
                </div>
            `;
        }).join('');
        html = `
            <div class="od-deck-shell ${animClass} ${pricingDensity.shell} flex flex-col justify-center">
                <div class="od-title-mark mx-auto"></div>
                ${createEditableTag('h2', 'text-5xl font-bold mb-4 w-full text-center text-white tracking-tight', slide.title, 'title')}
                ${createEditableTag('p', 'od-lead mb-14 w-full text-center block', slide.subtitle, 'subtitle')}
                <div class="od-pricing-grid ${pricingDensity.grid}">${tiersHtml}</div>
            </div>
        `;
    }
    // Example for Literature Review
    else if (slide.type === 'res_lit_review') {
        let themesHtml = (slide.themes || []).map((t, i) => `
            <div class="od-card">
                <div class="od-card__icon">${renderIconOrImage(t.icon, 'text-blue-400')}</div>
                ${createEditableTag('h4', 'text-lg font-bold text-white mb-2', t.title, 'title', 'themes', i)}
                ${createEditableTag('p', 'text-xs text-slate-400 leading-relaxed', t.text, 'text', 'themes', i)}
            </div>
        `).join('');
        html = `
            <div class="od-deck-shell ${animClass}">
                <div class="od-title-mark"></div>
                ${createEditableTag('h2', 'text-5xl font-black mb-4 tracking-tight', slide.title, 'title')}
                ${createEditableTag('p', 'od-lead mb-10 block', slide.subtitle, 'subtitle')}
                <div class="grid grid-cols-2 gap-6 w-full">${themesHtml}</div>
            </div>
        `;
    }
    // 2. Variables & Hypothesis (Flow Design)
else if (slide.type === 'res_variables') {
    let hypothesesHtml = (slide.hypotheses || []).map((h, i) => `
        <div class="bg-white/5 border border-white/10 p-5 rounded-2xl mb-4 flex items-center gap-5 shadow-sm">
            <div class="w-10 h-10 rounded-full bg-blue-600/20 flex items-center justify-center font-black text-blue-400 border border-blue-500/30 shrink-0">H${i+1}</div>
            ${createEditablePrimitive('span', 'text-xl text-slate-200 flex-grow leading-relaxed font-light', h, 'hypotheses', i)}
        </div>
    `).join('');
    html = `
        <div class="od-deck-shell ${animClass} !max-w-6xl">
            <div class="od-title-mark mx-auto"></div>
            ${createEditableTag('h2', 'text-6xl font-black mb-14 text-center text-white tracking-tight', slide.title, 'title')}
            <div class="flex items-center justify-center gap-12 mb-16 w-full">
                <div class="od-card !p-10 flex-1 text-center bg-blue-950/20 border-blue-500/30" style="--card-accent: #3B82F6">
                    <div class="text-[10px] uppercase tracking-[0.3em] text-blue-400 mb-4 font-black">Independent Variable (IV)</div>
                    ${createEditableTag('h3', 'text-4xl font-extrabold text-white leading-tight', slide.ivTitle, 'ivTitle')}
                </div>
                <div class="flex flex-col items-center gap-2">
                    <i class="fa-solid fa-arrow-right-long text-5xl text-slate-700"></i>
                    <span class="text-[9px] font-bold text-slate-600 uppercase tracking-widest">Prediction</span>
                </div>
                <div class="od-card !p-10 flex-1 text-center bg-emerald-950/20 border-emerald-500/30" style="--card-accent: #10B981">
                    <div class="text-[10px] uppercase tracking-[0.3em] text-emerald-400 mb-4 font-black">Dependent Variable (DV)</div>
                    ${createEditableTag('h3', 'text-4xl font-extrabold text-white leading-tight', slide.dvTitle, 'dvTitle')}
                </div>
            </div>
            <div class="max-w-4xl mx-auto w-full">${hypothesesHtml}</div>
        </div>
    `;
}

// 2. Scope & Limitations (High-Contrast Status)
else if (slide.type === 'res_limits') {
    let pointsHtml = (slide.points || []).map((p, i) => `
        <div class="flex items-start gap-8 p-6 rounded-3xl bg-slate-900/40 border border-white/5 mb-5 transition hover:border-white/20 hover:bg-slate-800/40">
            <div class="w-16 h-16 rounded-2xl flex items-center justify-center shrink-0 shadow-2xl p-4" style="background: color-mix(in srgb, ${p.color} 15%, transparent); border: 1px solid color-mix(in srgb, ${p.color} 30%, transparent)">
                ${renderIconOrImage(p.icon, "text-2xl", p.color)}
            </div>
            <div class="flex-grow">
                <div class="text-[11px] font-black uppercase tracking-[0.25em] mb-2" style="color: ${p.color}">${createEditableTag('span', '', p.label, 'label', 'points', i)}</div>
                ${createEditableTag('p', 'text-2xl text-white font-semibold leading-snug tracking-tight', p.text, 'text', 'points', i)}
            </div>
        </div>
    `).join('');
    html = `
        <div class="od-deck-shell ${animClass}">
            <div class="od-title-mark"></div>
            ${createEditableTag('h2', 'text-6xl font-black mb-6 text-white tracking-tighter', slide.title, 'title')}
            ${createEditableTag('p', 'od-lead mb-12 block text-2xl font-light', slide.subtitle, 'subtitle')}
            <div class="grid grid-cols-1 w-full max-w-5xl">${pointsHtml}</div>
        </div>
    `;
}

// 3. Final Takeaway (Aurora Impact Layout)
else if (slide.type === 'res_key_takeaway') {
    let pointsHtml = (slide.points || []).map((p, i) => `
        <div class="flex items-center gap-4 text-2xl text-blue-100/70 font-light">
            <i class="fa-solid fa-circle-check text-blue-500/50 text-sm"></i>
            ${createEditablePrimitive('span', '', p, 'points', i)}
        </div>
    `).join('');
    html = `
        <div class="od-orb od-orb--blue" style="filter: blur(120px); opacity: 0.4;"></div>
        <div class="od-shell ${animClass} text-center !bg-slate-950/20 !backdrop-blur-3xl border-white/5 shadow-2xl">
            <div class="mb-12 inline-flex items-center justify-center w-28 h-28 rounded-[2rem] bg-blue-600/10 border border-blue-400/20 shadow-[0_0_60px_rgba(59,130,246,0.2)] p-6">
                ${renderIconOrImage(slide.icon, 'text-6xl text-blue-400 drop-shadow-[0_0_25px_rgba(59,130,246,0.6)]')}
            </div>
            <div class="text-[12px] font-black uppercase tracking-[0.4em] text-blue-400/80 mb-6">Primary Conclusion</div>
            ${createEditableTag('h1', 'text-8xl font-black mb-10 tracking-tighter text-white leading-[0.9] drop-shadow-2xl', slide.title, 'title')}
            <div class="h-1 w-32 bg-gradient-to-r from-transparent via-blue-500 to-transparent mx-auto mb-12 opacity-50"></div>
            ${createEditableTag('p', 'text-4xl font-serif italic text-blue-50/90 leading-tight max-w-5xl mx-auto mb-14 block', slide.subtitle, 'subtitle')}
            <div class="flex flex-col items-center gap-6 py-8 border-t border-white/5 w-full">${pointsHtml}</div>
        </div>
    `;
}
else if (slide.type === 'custom_block_layout') {
        html = renderCustomBlockLayout(slide);
    }
    else {
        html = `<div class="theme-card ${animClass}">${createEditableTag('h2', 'text-4xl font-bold mb-10 w-full', slide.title, 'title')}</div>`;
    }

    // --- NEW: Persistent Company Logo Watermark ---
    let watermark = '';
    if (isExport && globalSettings.companyLogo) {
        watermark = `<img src="${globalSettings.companyLogo}" class="absolute bottom-8 right-10 max-h-10 max-w-[170px] object-contain z-0 opacity-25 pointer-events-none drop-shadow-lg select-none" aria-hidden="true">`;
    }

    // Wrap in slide-autofit so the content always fits the 1200×800 canvas
    return `<div class="slide-autofit relative" data-slide-autofit><div class="relative z-10 w-full h-full flex flex-col items-center justify-center">${html}</div>${watermark}</div>`;
}

function renderPreview() {
    const preview = document.getElementById('livePreview');
    const slide = slides.find(s => s.id === currentSlideId);
    if (!slide) { preview.innerHTML = ''; return; }
    preview.className = `preview-wrapper ${slide.bgOverride || 'bg-default'}`;
    preview.innerHTML = `<div class="theme-slide">${generateSlideHTML(slide, false)}</div>`;
    const themeSlide = preview.querySelector('.theme-slide');
    requestAnimationFrame(() => fitSlideContent(themeSlide));
    preview.querySelectorAll('img').forEach((image) => {
        if (!image.complete) {
            image.addEventListener('load', () => fitSlideContent(themeSlide), { once: true });
            image.addEventListener('error', () => fitSlideContent(themeSlide), { once: true });
        }
    });
}

// Explicitly expose to window
window.resizeImageForStorage = resizeImageForStorage;
window.injectRandomImage = injectRandomImage;
window.resizePreview = resizePreview;
window.fitSlideContent = fitSlideContent;
window.renderApp = renderApp;
window.renderSlideList = renderSlideList;
window.handleImageUpload = handleImageUpload;
window.renderEditor = renderEditor;
window.handleInlineEdit = handleInlineEdit;
window.handleInlineEditPrimitive = handleInlineEditPrimitive;
window.createEditableTag = createEditableTag;
window.createEditablePrimitive = createEditablePrimitive;
window.updateSlide = updateSlide;
window.updateArrayItem = updateArrayItem;
window.updateArrayPrimitive = updateArrayPrimitive;
window.addArrayItem = addArrayItem;
window.addArrayPrimitive = addArrayPrimitive;
window.removeArrayItem = removeArrayItem;
window.removeArrayPrimitive = removeArrayPrimitive;
window.removeImage = removeImage;
window.addSlide = addSlide;
window.duplicateSlide = duplicateSlide;
window.deleteSlide = deleteSlide;
window.escapeHtml = escapeHtml;
window.generateSlideHTML = generateSlideHTML;
window.renderPreview = renderPreview;