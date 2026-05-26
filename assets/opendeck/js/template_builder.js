// opendeck/js/template_builder.js

class AdvancedTemplateBuilder {
    constructor() {
        this.layout = []; // Array of Rows
        this.activeElement = null; // { rIndex, cIndex, bIndex }
        this.slideSettings = { bgOverride: 'bg-slate-900', padding: 'p-12', verticalAlign: 'justify-center' };
        
        this.blockTypes = {
            'heading': { icon: 'fa-heading', label: 'Heading', default: { text: 'Custom Heading', size: 'text-5xl', align: 'text-left', color: 'text-white' } },
            'text': { icon: 'fa-align-left', label: 'Paragraph', default: { text: 'Enter your supporting text here. Drag elements to build complex grids.', size: 'text-lg', align: 'text-left', color: 'text-slate-300' } },
            'icon': { icon: 'fa-icons', label: 'Icon', default: { iconClass: 'fa-rocket', size: 'text-6xl', align: 'text-left', color: 'text-blue-500' } },
            'metric': { icon: 'fa-chart-simple', label: 'Data Metric', default: { value: '84%', label: 'Growth Rate', align: 'text-center', color: 'text-blue-400' } },
            'image': { icon: 'fa-image', label: 'Image Box', default: { style: 'rounded-xl border border-slate-700 bg-slate-800 aspect-video flex items-center justify-center' } },
            'spacer': { icon: 'fa-arrows-up-down', label: 'Spacer', default: { height: 'h-8' } },
            'divider': { icon: 'fa-minus', label: 'Divider', default: { style: 'border-t border-slate-700 my-4 w-full' } }
        };

        this.draggedBlockType = null;
        this.mountUI();
    }

    mountUI() {
        const existing = document.getElementById('advancedBuilderModal');
        if (existing) existing.remove();

        const modalHtml = `
        <div class="modal-overlay" id="advancedBuilderModal" style="display:none; z-index: 9999;">
            <div class="modal-content !w-[1400px] !max-w-[95vw] !h-[90vh] !p-0 bg-slate-900 border border-slate-700 flex flex-col shadow-2xl overflow-hidden">
                
                <div class="h-14 border-b border-slate-800 flex justify-between items-center bg-slate-950 px-4 shrink-0">
                    <h2 class="text-lg font-extrabold text-white flex items-center">
                        <i class="fa-solid fa-wand-magic-sparkles text-blue-500 mr-3"></i> Papyrus Layout Engine
                    </h2>
                    <div class="flex items-center gap-3">
                        <input type="text" id="tb_templateName" placeholder="Template Name..." class="bg-slate-800 border border-slate-700 text-white px-3 py-1.5 rounded text-sm outline-none focus:border-blue-500 w-64">
                        <button onclick="window.templateBuilder.close()" class="text-slate-400 hover:text-white px-3 py-1 text-sm font-bold">Cancel</button>
                        <button onclick="window.templateBuilder.saveTemplate()" class="bg-blue-600 hover:bg-blue-500 text-white px-5 py-1.5 rounded font-bold shadow-lg flex items-center gap-2 text-sm transition">
                            <i class="fa-solid fa-check"></i> Save & Compile
                        </button>
                    </div>
                </div>

                <div class="flex flex-grow overflow-hidden h-full">
                    
                    <div class="w-72 bg-slate-950 border-r border-slate-800 p-4 flex flex-col shrink-0 overflow-y-auto">
                        <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-3"><i class="fa-solid fa-border-all mr-1"></i> 1. Add Row Grids</h3>
                        <div class="grid grid-cols-2 gap-2 mb-8">
                            <button onclick="window.templateBuilder.addRow('100')" class="bg-slate-800 hover:bg-slate-700 border border-slate-600 p-2 rounded text-white text-xs font-bold flex flex-col items-center gap-1.5 transition-colors col-span-2">
                                <div class="w-full h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div> Full Width
                            </button>
                            <button onclick="window.templateBuilder.addRow('50-50')" class="bg-slate-800 hover:bg-slate-700 border border-slate-600 p-2 rounded text-white text-xs font-bold flex flex-col items-center gap-1.5 transition-colors">
                                <div class="flex w-full gap-1"><div class="flex-1 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div><div class="flex-1 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div></div> 50 / 50
                            </button>
                            <button onclick="window.templateBuilder.addRow('30-70')" class="bg-slate-800 hover:bg-slate-700 border border-slate-600 p-2 rounded text-white text-xs font-bold flex flex-col items-center gap-1.5 transition-colors">
                                <div class="flex w-full gap-1"><div class="w-1/3 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div><div class="w-2/3 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div></div> 30 / 70
                            </button>
                            <button onclick="window.templateBuilder.addRow('70-30')" class="bg-slate-800 hover:bg-slate-700 border border-slate-600 p-2 rounded text-white text-xs font-bold flex flex-col items-center gap-1.5 transition-colors">
                                <div class="flex w-full gap-1"><div class="w-2/3 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div><div class="w-1/3 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div></div> 70 / 30
                            </button>
                            <button onclick="window.templateBuilder.addRow('33-33-33')" class="bg-slate-800 hover:bg-slate-700 border border-slate-600 p-2 rounded text-white text-xs font-bold flex flex-col items-center gap-1.5 transition-colors col-span-2">
                                <div class="flex w-full gap-1"><div class="flex-1 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div><div class="flex-1 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div><div class="flex-1 h-3 bg-blue-500/30 border border-blue-500/50 rounded"></div></div> 3 Columns
                            </button>
                        </div>

                        <h3 class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-3"><i class="fa-solid fa-cube mr-1"></i> 2. Drag Elements</h3>
                        <div class="flex flex-col gap-2">
                            ${Object.entries(this.blockTypes).map(([type, data]) => `
                                <div draggable="true" ondragstart="window.templateBuilder.handleDragStart(event, '${type}')" 
                                     class="bg-slate-800 border border-slate-700 p-3 rounded flex items-center gap-3 cursor-grab hover:border-blue-500 hover:bg-slate-700 transition text-slate-300 hover:text-white shadow-sm">
                                    <i class="fa-solid ${data.icon} w-5 text-center text-blue-400"></i> <span class="text-sm font-bold">${data.label}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>

                    <div class="flex-grow bg-[#020617] overflow-y-auto relative p-8 flex justify-center items-start shadow-inner" id="tb_canvasArea" onclick="window.templateBuilder.selectElement(null)">
                        
                        <div id="builderCanvas" class="w-full aspect-video shrink-0 border border-slate-700 rounded-lg shadow-2xl relative flex flex-col overflow-y-auto overflow-x-hidden transition-colors duration-300" onclick="event.stopPropagation(); window.templateBuilder.selectElement(null)">
                            </div>

                    </div>

                    <div class="w-[320px] bg-slate-950 border-l border-slate-800 flex flex-col shrink-0 shadow-xl" id="tb_inspector" onclick="event.stopPropagation()">
                        </div>

                </div>
            </div>
        </div>`;
        document.body.insertAdjacentHTML('beforeend', modalHtml);
    }

    open() {
        this.layout = [];
        this.activeElement = null;
        if(document.getElementById('tb_templateName')) document.getElementById('tb_templateName').value = '';
        this.renderCanvas();
        this.renderInspector();
        
        if (typeof window.showModal === 'function') window.showModal('advancedBuilderModal');
        else {
            const m = document.getElementById('advancedBuilderModal');
            m.style.display = 'flex';
            m.classList.add('show');
        }
    }

    close() {
        if (typeof window.hideModal === 'function') window.hideModal('advancedBuilderModal');
        else {
            const m = document.getElementById('advancedBuilderModal');
            m.classList.remove('show');
            setTimeout(() => m.style.display = 'none', 300);
        }
        setTimeout(() => { if (typeof window.showModal === 'function') window.showModal('templateModal'); }, 300);
    }

    // --- GRID MANAGEMENT (Now includes Move Up / Move Down) ---
    addRow(layoutType) {
        let cols = [];
        if (layoutType === '100') cols = [{ width: '100%', blocks: [] }];
        else if (layoutType === '50-50') cols = [{ width: '50%', blocks: [] }, { width: '50%', blocks: [] }];
        else if (layoutType === '30-70') cols = [{ width: '30%', blocks: [] }, { width: '70%', blocks: [] }];
        else if (layoutType === '70-30') cols = [{ width: '70%', blocks: [] }, { width: '30%', blocks: [] }];
        else if (layoutType === '33-33-33') cols = [{ width: '33.333%', blocks: [] }, { width: '33.333%', blocks: [] }, { width: '33.333%', blocks: [] }];

        this.layout.push({ id: 'row_' + Date.now(), cols: cols });
        this.renderCanvas();
    }

    moveRowUp(rIndex) {
        if (rIndex === 0) return;
        const temp = this.layout[rIndex - 1];
        this.layout[rIndex - 1] = this.layout[rIndex];
        this.layout[rIndex] = temp;
        this.activeElement = null; // Clear selection to prevent weird state bounds
        this.renderCanvas();
        this.renderInspector();
    }

    moveRowDown(rIndex) {
        if (rIndex === this.layout.length - 1) return;
        const temp = this.layout[rIndex + 1];
        this.layout[rIndex + 1] = this.layout[rIndex];
        this.layout[rIndex] = temp;
        this.activeElement = null;
        this.renderCanvas();
        this.renderInspector();
    }

    deleteRow(rIndex) {
        this.layout.splice(rIndex, 1);
        if (this.activeElement && this.activeElement.rIndex === rIndex) this.activeElement = null;
        this.renderCanvas();
        this.renderInspector();
    }

    deleteBlock(rIndex, cIndex, bIndex) {
        this.layout[rIndex].cols[cIndex].blocks.splice(bIndex, 1);
        this.activeElement = null;
        this.renderCanvas();
        this.renderInspector();
    }

    // --- DRAG AND DROP ---
    handleDragStart(e, type) {
        this.draggedBlockType = type;
        e.dataTransfer.setData('text/plain', type);
        e.dataTransfer.effectAllowed = 'copy';
    }

    allowDrop(e) { 
        e.preventDefault(); 
        e.currentTarget.classList.add('bg-blue-500/20', 'border-blue-400'); 
    }
    leaveDrop(e) { 
        e.currentTarget.classList.remove('bg-blue-500/20', 'border-blue-400'); 
    }

    handleDrop(e, rIndex, cIndex) {
        e.preventDefault();
        e.currentTarget.classList.remove('bg-blue-500/20', 'border-blue-400'); 
        const type = this.draggedBlockType;
        if (!type || !this.blockTypes[type]) return;

        const newBlock = { type: type, props: JSON.parse(JSON.stringify(this.blockTypes[type].default)) };
        this.layout[rIndex].cols[cIndex].blocks.push(newBlock);
        
        this.activeElement = { rIndex, cIndex, bIndex: this.layout[rIndex].cols[cIndex].blocks.length - 1 };
        this.draggedBlockType = null;
        this.renderCanvas();
        this.renderInspector();
    }

    // --- INTERACTION ---
    selectElement(elementData, event) {
        if(event) event.stopPropagation();
        this.activeElement = elementData;
        this.renderCanvas();
        this.renderInspector();
    }

    updateBlockProp(key, value) {
        if (!this.activeElement) return;
        const { rIndex, cIndex, bIndex } = this.activeElement;
        this.layout[rIndex].cols[cIndex].blocks[bIndex].props[key] = value;
        this.renderCanvas();
    }

    updateSlideSetting(key, value) {
        this.slideSettings[key] = value;
        this.renderCanvas();
    }

    // --- RENDERING ---
    renderCanvas() {
        const canvas = document.getElementById('builderCanvas');
        
        // Use aspect-video to force 16:9 natively
        canvas.className = `w-full aspect-video shrink-0 border border-slate-700 rounded-lg shadow-2xl relative flex flex-col overflow-y-auto overflow-x-hidden transition-colors duration-300 ${this.slideSettings.bgOverride} ${this.slideSettings.padding}`;
        if(this.slideSettings.verticalAlign === 'justify-center') canvas.classList.add('justify-center');
        if(this.slideSettings.verticalAlign === 'justify-between') canvas.classList.add('justify-between');

        if (this.layout.length === 0) {
            canvas.innerHTML = `
                <div class="absolute inset-0 flex flex-col items-center justify-center text-slate-500 pointer-events-none">
                    <i class="fa-solid fa-border-all text-6xl mb-4 opacity-20 text-blue-500"></i>
                    <p class="text-xl font-bold text-slate-400">Blank Canvas</p>
                    <p class="text-sm opacity-70 mt-2">Click a Row Grid from the left panel to begin.</p>
                </div>`;
            return;
        }

        let html = '';
        this.layout.forEach((row, rIndex) => {
            let colsHtml = row.cols.map((col, cIndex) => {
                
                let blocksHtml = col.blocks.map((block, bIndex) => {
                    const isActive = this.activeElement && this.activeElement.rIndex === rIndex && this.activeElement.cIndex === cIndex && this.activeElement.bIndex === bIndex;
                    const blockUi = this.generateBlockPreview(block.type, block.props);
                    
                    return `
                    <div class="relative group cursor-pointer border-2 ${isActive ? 'border-blue-500 bg-blue-500/5' : 'border-transparent hover:border-slate-500 border-dashed'} transition-all duration-200 p-1 -m-1 rounded" 
                         onclick="window.templateBuilder.selectElement({rIndex: ${rIndex}, cIndex: ${cIndex}, bIndex: ${bIndex}}, event)">
                        ${isActive ? `<button onclick="window.templateBuilder.deleteBlock(${rIndex}, ${cIndex}, ${bIndex})" class="absolute -top-3 -right-3 w-6 h-6 bg-red-500 text-white rounded-full z-50 shadow text-xs flex items-center justify-center"><i class="fa-solid fa-trash"></i></button>` : ''}
                        ${blockUi}
                    </div>`;
                }).join('');

                const isEmpty = col.blocks.length === 0;

                return `
                <div style="width: ${col.width};" class="border-2 border-slate-700/50 hover:border-blue-500/50 border-dashed min-h-[100px] flex flex-col gap-3 p-3 transition-colors rounded relative"
                     ondragover="window.templateBuilder.allowDrop(event)"
                     ondragleave="window.templateBuilder.leaveDrop(event)"
                     ondrop="window.templateBuilder.handleDrop(event, ${rIndex}, ${cIndex})">
                    ${!isEmpty ? blocksHtml : `
                        <div class="absolute inset-0 flex flex-col items-center justify-center text-slate-600 pointer-events-none">
                            <i class="fa-solid fa-download mb-2 opacity-50"></i>
                            <span class="text-[10px] uppercase font-bold tracking-widest">Drop Here</span>
                        </div>
                    `}
                </div>`;
            }).join('');

            // Added Floating Controls: Move Up, Move Down, Delete
            html += `
            <div class="relative group border-2 border-transparent hover:border-slate-600 border-dashed mt-4 mb-4 p-1 rounded transition-colors w-full shrink-0">
                
                <div class="absolute right-2 -top-5 flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity z-[60] bg-slate-800 p-1.5 rounded shadow-lg border border-slate-600">
                    <button onclick="window.templateBuilder.moveRowUp(${rIndex})" class="w-6 h-6 hover:bg-slate-700 text-slate-300 hover:text-white rounded flex items-center justify-center disabled:opacity-30 disabled:hover:bg-transparent" ${rIndex === 0 ? 'disabled' : ''} title="Move Row Up"><i class="fa-solid fa-chevron-up"></i></button>
                    <button onclick="window.templateBuilder.moveRowDown(${rIndex})" class="w-6 h-6 hover:bg-slate-700 text-slate-300 hover:text-white rounded flex items-center justify-center disabled:opacity-30 disabled:hover:bg-transparent" ${rIndex === this.layout.length - 1 ? 'disabled' : ''} title="Move Row Down"><i class="fa-solid fa-chevron-down"></i></button>
                    <div class="w-px h-4 bg-slate-600 mx-1"></div>
                    <button onclick="window.templateBuilder.deleteRow(${rIndex})" class="w-6 h-6 hover:bg-red-500/20 text-red-400 hover:text-red-300 rounded flex items-center justify-center" title="Delete Row"><i class="fa-solid fa-trash"></i></button>
                </div>

                <div class="flex flex-row gap-6 w-full">
                    ${colsHtml}
                </div>
            </div>`;
        });

        canvas.innerHTML = html;
    }

    generateBlockPreview(type, props) {
        const align = props.align || 'text-left';
        const color = props.color || 'text-white';
        const size = props.size || 'text-base';

        if (type === 'heading') return `<h2 class="font-bold leading-tight w-full ${size} ${align} ${color}">${props.text}</h2>`;
        if (type === 'text') return `<p class="w-full leading-relaxed ${size} ${align} ${color}">${props.text}</p>`;
        if (type === 'icon') return `<div class="w-full ${align}"><i class="fa-solid ${props.iconClass} ${size} ${color}"></i></div>`;
        if (type === 'spacer') return `<div class="w-full ${props.height}"></div>`;
        if (type === 'divider') return `<hr class="${props.style}">`;
        if (type === 'image') return `<div class="w-full ${props.style}"><i class="fa-solid fa-image text-4xl text-slate-500"></i></div>`;
        if (type === 'metric') return `<div class="w-full ${align}"><div class="text-5xl font-black ${color} tracking-tighter mb-1">${props.value}</div><div class="text-xs font-bold text-slate-400 uppercase tracking-widest">${props.label}</div></div>`;
        return `<div>Unknown Block</div>`;
    }

    renderInspector() {
        const inspector = document.getElementById('tb_inspector');
        
        if (!this.activeElement) {
            inspector.innerHTML = `
                <div class="p-4 border-b border-slate-800 bg-slate-900"><h3 class="text-xs font-bold text-slate-400 uppercase tracking-widest"><i class="fa-solid fa-sliders mr-2"></i> Slide Settings</h3></div>
                <div class="p-4 space-y-4 overflow-y-auto flex-grow custom-scrollbar">
                    <div>
                        <label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Background Theme</label>
                        <select onchange="window.templateBuilder.updateSlideSetting('bgOverride', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500">
                            <option value="bg-slate-900" ${this.slideSettings.bgOverride === 'bg-slate-900' ? 'selected' : ''}>Standard Slate</option>
                            <option value="bg-slate-950" ${this.slideSettings.bgOverride === 'bg-slate-950' ? 'selected' : ''}>Pitch Black</option>
                            <option value="bg-blue-950" ${this.slideSettings.bgOverride === 'bg-blue-950' ? 'selected' : ''}>Deep Blue</option>
                            <option value="bg-white" ${this.slideSettings.bgOverride === 'bg-white' ? 'selected' : ''}>Solid White</option>
                        </select>
                    </div>
                    <div>
                        <label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Vertical Alignment</label>
                        <select onchange="window.templateBuilder.updateSlideSetting('verticalAlign', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500">
                            <option value="justify-center" ${this.slideSettings.verticalAlign === 'justify-center' ? 'selected' : ''}>Center Vertically</option>
                            <option value="justify-start" ${this.slideSettings.verticalAlign === 'justify-start' ? 'selected' : ''}>Align Top</option>
                        </select>
                    </div>
                    <div>
                        <label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Padding</label>
                        <select onchange="window.templateBuilder.updateSlideSetting('padding', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500">
                            <option value="p-12" ${this.slideSettings.padding === 'p-12' ? 'selected' : ''}>Standard (p-12)</option>
                            <option value="p-0" ${this.slideSettings.padding === 'p-0' ? 'selected' : ''}>None (Full Bleed)</option>
                            <option value="p-20" ${this.slideSettings.padding === 'p-20' ? 'selected' : ''}>Extra Large (p-20)</option>
                        </select>
                    </div>
                </div>
            `;
            return;
        }

        const { rIndex, cIndex, bIndex } = this.activeElement;
        const block = this.layout[rIndex].cols[cIndex].blocks[bIndex];
        
        let fieldsHtml = '';
        
        if (block.props.text !== undefined) {
            fieldsHtml += `
                <div><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Text Content</label>
                <textarea oninput="window.templateBuilder.updateBlockProp('text', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm min-h-[100px] outline-none focus:border-blue-500">${block.props.text}</textarea></div>`;
        }
        
        if (block.props.iconClass !== undefined) {
            fieldsHtml += `
                <div><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">FontAwesome Class</label>
                <input type="text" value="${block.props.iconClass}" oninput="window.templateBuilder.updateBlockProp('iconClass', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500 font-mono"></div>`;
        }

        if (block.props.value !== undefined) {
            fieldsHtml += `
                <div><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Data Value</label>
                <input type="text" value="${block.props.value}" oninput="window.templateBuilder.updateBlockProp('value', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500"></div>
                <div class="mt-4"><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Data Label</label>
                <input type="text" value="${block.props.label}" oninput="window.templateBuilder.updateBlockProp('label', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500"></div>`;
        }

        if (block.props.size !== undefined) {
            const sizes = ['text-xs', 'text-sm', 'text-base', 'text-lg', 'text-xl', 'text-2xl', 'text-3xl', 'text-4xl', 'text-5xl', 'text-6xl', 'text-7xl'];
            fieldsHtml += `
                <div><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Font Size</label>
                <select onchange="window.templateBuilder.updateBlockProp('size', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500">
                    ${sizes.map(s => `<option value="${s}" ${block.props.size === s ? 'selected' : ''}>${s}</option>`).join('')}
                </select></div>`;
        }

        if (block.props.align !== undefined) {
            const aligns = [{v: 'text-left', l: '<i class="fa-solid fa-align-left"></i>'}, {v: 'text-center', l: '<i class="fa-solid fa-align-center"></i>'}, {v: 'text-right', l: '<i class="fa-solid fa-align-right"></i>'}];
            fieldsHtml += `
                <div><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Alignment</label>
                <div class="flex gap-2">
                    ${aligns.map(a => `<button onclick="window.templateBuilder.updateBlockProp('align', '${a.v}')" class="flex-1 py-1.5 rounded text-sm font-bold transition-colors ${block.props.align === a.v ? 'bg-blue-600 text-white border-blue-500' : 'bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700'}">${a.l}</button>`).join('')}
                </div></div>`;
        }

        if (block.props.color !== undefined) {
            const colors = ['text-white', 'text-slate-900', 'text-slate-400', 'text-blue-500', 'text-emerald-400', 'text-purple-400', 'text-orange-400', 'text-red-500'];
            fieldsHtml += `
                <div><label class="text-[10px] font-bold text-slate-500 uppercase block mb-2">Color</label>
                <select onchange="window.templateBuilder.updateBlockProp('color', this.value)" class="w-full bg-slate-800 border border-slate-700 text-white rounded px-3 py-2 text-sm outline-none focus:border-blue-500">
                    ${colors.map(c => `<option value="${c}" ${block.props.color === c ? 'selected' : ''}>${c.replace('text-', '')}</option>`).join('')}
                </select></div>`;
        }

        inspector.innerHTML = `
            <div class="p-4 border-b border-slate-800 bg-slate-900 flex justify-between items-center">
                <h3 class="text-xs font-bold text-white uppercase tracking-widest flex items-center"><i class="fa-solid fa-pen-to-square mr-2 text-blue-500"></i> Edit ${this.blockTypes[block.type].label}</h3>
            </div>
            <div class="p-5 space-y-6 overflow-y-auto flex-grow custom-scrollbar bg-slate-950">
                ${fieldsHtml}
            </div>
        `;
    }

    compileToHTML() {
        let html = `<div class="w-full h-full flex flex-col ${this.slideSettings.verticalAlign} ${this.slideSettings.padding}">`;
        
        this.layout.forEach(row => {
            html += `<div class="flex flex-row w-full gap-8 mb-6">`;
            row.cols.forEach(col => {
                html += `<div style="width: ${col.width};" class="flex flex-col gap-4">`;
                col.blocks.forEach(block => {
                    html += this.generateBlockPreview(block.type, block.props);
                });
                html += `</div>`;
            });
            html += `</div>`;
        });
        
        html += `</div>`;
        return html;
    }

    saveTemplate() {
        if (this.layout.length === 0) return alert("Please add at least one row to the layout.");
        
        const templateName = document.getElementById('tb_templateName').value.trim() || 'Custom Layout ' + Math.floor(Math.random() * 1000);
        
        const slideData = {
            type: 'custom_baked_template',
            navName: templateName,
            title: templateName,
            bgOverride: this.slideSettings.bgOverride,
            htmlContent: this.compileToHTML()
        };

        const templateObj = {
            id: 'tmpl_' + Date.now(),
            name: templateName,
            slideData: slideData,
            layout: this.layout 
        };

        if (!window.customTemplates) window.customTemplates = [];
        window.customTemplates.push(templateObj);
        
        if (window.papyrusBridge && window.papyrusBridge.saveCustomTemplate) {
            window.papyrusBridge.saveCustomTemplate(JSON.stringify(templateObj));
        }

        this.close();
        
        setTimeout(() => {
            if (typeof window.switchTemplateTab === 'function') {
                window.switchTemplateTab('custom');
                if (typeof window.renderCustomTemplateTab === 'function') window.renderCustomTemplateTab();
            }
        }, 350);
    }
}

function initTemplateBuilder() {
    if (!window.templateBuilder) {
        window.templateBuilder = new AdvancedTemplateBuilder();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initTemplateBuilder);
} else {
    initTemplateBuilder();
}