// Signature Manager - Mobile-First
class SignatureManager {
    constructor() {
        this.canvas = null;
        this.ctx = null;
        this.isDrawing = false;
        this.points = [];
        this.signatures = [];
        this.assignments = {};
        this.deviceId = this.getOrCreateDeviceId();
        this.deviceName = this.getDeviceName();
        
        this.init();
    }
    
    init() {
        // Always attach event listeners first - critical for button functionality
        this.attachEventListeners();
        this.checkOnlineStatus();
        
        window.addEventListener('online', () => this.handleOnline());
        window.addEventListener('offline', () => this.handleOffline());
        
        this.loadAllData();
        this.loadLocalSignatures();
    }
    
    getOrCreateDeviceId() {
        let deviceId = localStorage.getItem('device_id');
        if (!deviceId) {
            deviceId = 'mobile_' + Math.random().toString(36).substr(2, 12);
            localStorage.setItem('device_id', deviceId);
        }
        return deviceId;
    }
    
    getDeviceName() {
        const ua = navigator.userAgent;
        if (/iPhone/.test(ua)) return 'iPhone';
        if (/Android/.test(ua)) return 'Android Device';
        if (/iPad/.test(ua)) return 'iPad';
        if (/Windows/.test(ua)) return 'Windows PC';
        if (/Mac/.test(ua)) return 'Mac';
        return 'Unknown Device';
    }

    setupCanvas() {
        if (!this.canvas) {
            console.error('Canvas element not found in setupCanvas');
            return;
        }

        const parent = this.canvas.parentElement;
        const rect = parent.getBoundingClientRect();

        // CSS size (what the user sees)
        const cssWidth = Math.max(300, Math.min(700, rect.width));
        const cssHeight = 200;

        // HiDPI backing store (what we actually draw into)
        const dpr = window.devicePixelRatio || 1;

        this.canvas.style.width = cssWidth + 'px';
        this.canvas.style.height = cssHeight + 'px';
        this.canvas.width = Math.round(cssWidth * dpr);
        this.canvas.height = Math.round(cssHeight * dpr);

        this._sigCssWidth = cssWidth;
        this._sigCssHeight = cssHeight;

        console.log(`Canvas initialized (CSS ${cssWidth}x${cssHeight}, DPR ${dpr}, backing ${this.canvas.width}x${this.canvas.height})`);

        this.ctx = this.canvas.getContext('2d', { alpha: true });

        // Drawing style (in device pixels)
        this._baseWidthCss = 3.0;
        this._minWidthCss = 2.0;
        this._maxWidthCss = 4.5;
        this._lastTs = 0;

        this.ctx.strokeStyle = '#000';
        this.ctx.lineCap = 'round';
        this.ctx.lineJoin = 'round';
        this.ctx.miterLimit = 2;

        // Prevent iOS scrolling/zooming inside canvas
        this.canvas.style.touchAction = 'none';
        this.canvas.style.webkitUserSelect = 'none';
        this.canvas.style.userSelect = 'none';
    }

    
    attachEventListeners() {
        const createBtn = document.getElementById('createSignatureBtn');
        if (createBtn) {
            createBtn.addEventListener('click', () => this.openCreateModal());
        }
        
        const closeBtn = document.getElementById('closeModalBtn');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.closeCreateModal());
        }
        
        const clearBtn = document.getElementById('clearCanvasBtn');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => this.clearCanvas());
        }
        
        const form = document.getElementById('createSignatureForm');
        if (form) {
            form.addEventListener('submit', (e) => this.saveSignature(e));
        }
        
        const autoAssignBtn = document.getElementById('autoAssignBtn');
        if (autoAssignBtn) {
            autoAssignBtn.addEventListener('click', () => this.autoAssign());
        }
        
        const syncBtn = document.getElementById('syncSignaturesBtn');
        if (syncBtn) {
            syncBtn.addEventListener('click', () => this.syncSignatures());
        }
    }
    
    handleTouchStart(e) {
        e.preventDefault();
        const touch = e.touches[0];
        const rect = this.canvas.getBoundingClientRect();
        const x = touch.clientX - rect.left;
        const y = touch.clientY - rect.top;
        
        this.isDrawing = true;
        this.ctx.beginPath();
        this.ctx.moveTo(x, y);
        this.points.push({x, y});
        
        console.log('Touch start at:', x, y);
    }
    
    handleTouchMove(e) {
        if (!this.isDrawing) return;
        e.preventDefault();
        
        const touch = e.touches[0];
        const rect = this.canvas.getBoundingClientRect();
        const x = touch.clientX - rect.left;
        const y = touch.clientY - rect.top;
        
        this.ctx.lineTo(x, y);
        this.ctx.stroke();
        this.points.push({x, y});
    }

    startDrawing(e) {
        this.isDrawing = true;

        const p = this.getPosition(e);
        this.points = [p];
        this._lastTs = p.t || 0;

        // Start a new stroke
        this.ctx.beginPath();
        this.ctx.moveTo(p.x, p.y);

        // Draw a tiny dot for taps
        const dpr = window.devicePixelRatio || 1;
        this.ctx.lineWidth = this._baseWidthCss * dpr;
        this.ctx.lineTo(p.x + 0.01, p.y + 0.01);
        this.ctx.stroke();
        this.ctx.beginPath();
        this.ctx.moveTo(p.x, p.y);
    }

    draw(e) {
        if (!this.isDrawing) return;

        const p = this.getPosition(e);
        const last = this.points[this.points.length - 1];

        // If the browser is coalescing too hard, interpolate points so curves stay smooth
        const dx = p.x - last.x;
        const dy = p.y - last.y;
        const dist = Math.hypot(dx, dy);

        const step = 2.0 * (window.devicePixelRatio || 1); // 2px in device space
        const n = dist > step ? Math.ceil(dist / step) : 1;

        for (let i = 1; i <= n; i++) {
            const ip = {
                x: last.x + (dx * i / n),
                y: last.y + (dy * i / n),
                t: (last.t || p.t) + ((p.t - (last.t || p.t)) * i / n)
            };
            this._addPointAndRender(ip);
        }
    }

    stopDrawing() {
        if (!this.isDrawing) return;
        this.isDrawing = false;

        // Finish the stroke cleanly
        if (this.points.length > 2) {
            const p1 = this.points[this.points.length - 2];
            const p2 = this.points[this.points.length - 1];
            this.ctx.beginPath();
            this.ctx.moveTo(p1.x, p1.y);
            this.ctx.lineTo(p2.x, p2.y);
            this.ctx.stroke();
        }

        console.log('Drawing stopped. Points captured:', this.points.length);
    }

    _addPointAndRender(p) {
        this.points.push(p);

        const dpr = window.devicePixelRatio || 1;

        // Variable width: slower = thicker, faster = thinner (more pen-like)
        const prev = this.points.length >= 2 ? this.points[this.points.length - 2] : p;
        const dt = Math.max(1, (p.t || 0) - (prev.t || 0));
        const d = Math.hypot(p.x - prev.x, p.y - prev.y);
        const v = d / dt; // px per ms

        const minW = this._minWidthCss * dpr;
        const maxW = this._maxWidthCss * dpr;

        // Map velocity to width (tuned for touch + stylus)
        // v ~ 0.2 slow, ~2.0 fast
        const clampedV = Math.min(2.5, Math.max(0.05, v));
        const w = maxW - (clampedV / 2.5) * (maxW - minW);

        // Spline smoothing using midpoint-quadratic method (very smooth, low "corners")
        const pts = this.points;
        const len = pts.length;

        // Need at least 3 points to smooth
        if (len < 3) {
            this.ctx.lineWidth = w;
            this.ctx.beginPath();
            this.ctx.moveTo(prev.x, prev.y);
            this.ctx.lineTo(p.x, p.y);
            this.ctx.stroke();
            return;
        }

        const p0 = pts[len - 3];
        const p1 = pts[len - 2];
        const p2 = pts[len - 1];

        const m1 = { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 };
        const m2 = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };

        this.ctx.lineWidth = w;
        this.ctx.beginPath();
        this.ctx.moveTo(m1.x, m1.y);
        this.ctx.quadraticCurveTo(p1.x, p1.y, m2.x, m2.y);
        this.ctx.stroke();
    }


    getPosition(e) {
        // Map client coords -> canvas backing store coords (device pixels)
        const rect = this.canvas.getBoundingClientRect();
        const clientX = (e.touches && e.touches[0]) ? e.touches[0].clientX : e.clientX;
        const clientY = (e.touches && e.touches[0]) ? e.touches[0].clientY : e.clientY;

        const scaleX = this.canvas.width / rect.width;
        const scaleY = this.canvas.height / rect.height;

        return {
            x: (clientX - rect.left) * scaleX,
            y: (clientY - rect.top) * scaleY,
            t: (typeof e.timeStamp === 'number' ? e.timeStamp : performance.now())
        };
    }

    
    clearCanvas() {
        if (!this.ctx || !this.canvas) {
            console.warn('Canvas not initialized, skipping clear');
            return;
        }
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.points = [];
        console.log('Canvas cleared');
    }
    
    openCreateModal() {
        const modal = document.getElementById('createModal');
        if (!modal) {
            console.error('createModal element not found!');
            return;
        }
        
        // Show modal first
        modal.classList.add('show');
        
        // Wait for modal to be fully visible, then initialize canvas
        setTimeout(() => {
            this.canvas = document.getElementById('signatureCanvas');
            if (this.canvas) {
                console.log('Initializing canvas...');
                this.setupCanvas();
                this.clearCanvas();
            } else {
                console.error('signatureCanvas element not found!');
            }
        }, 100); // Small delay to ensure modal is rendered
        
        // Clear form inputs
        const nameInput = document.getElementById('signatureName');
        const roleInput = document.getElementById('signatureRole');
        
        if (nameInput) nameInput.value = '';
        if (roleInput) roleInput.value = '';
    }
    
    closeCreateModal() {
        const modal = document.getElementById('createModal');
        if (modal) {
            modal.classList.remove('show');
        }
        
        // Clean up canvas reference
        this.canvas = null;
        this.ctx = null;
        this.points = [];
    }
    
    async saveSignature(e) {
        e.preventDefault();
        
        const name = document.getElementById('signatureName').value.trim();
        const role = document.getElementById('signatureRole').value.trim();
        
        if (!name) {
            alert('Please enter a signature name');
            return;
        }
        
        if (this.points.length < 10) {
            alert('Please draw your signature');
            return;
        }
        
        const base64 = this.canvas.toDataURL('image/png').split(',')[1];
        
        const signatureData = {
            local_id: 'local_' + Date.now(),
            name: name,
            role: role,
            signature_base64: base64,
            device_id: this.deviceId,
            device_name: this.deviceName,
            created: new Date().toISOString()
        };
        
        this.saveToLocalStorage(signatureData);
        
        if (navigator.onLine) {
            const saved = await this.uploadSignature(signatureData);
            if (saved) {
                this.closeCreateModal();
                await this.loadAllData();
                this.showAlert('✅ Signature saved successfully!', 'success');
            } else {
                this.showAlert('⚠️ Signature saved locally. Will sync when online.', 'warning');
                this.closeCreateModal();
            }
        } else {
            this.showAlert('📱 Signature saved to your phone. Will sync when online.', 'info');
            this.closeCreateModal();
        }
    }
    
    saveToLocalStorage(signatureData) {
        let localSignatures = JSON.parse(localStorage.getItem('local_signatures') || '[]');
        localSignatures.push(signatureData);
        localStorage.setItem('local_signatures', JSON.stringify(localSignatures));
    }
    
    loadLocalSignatures() {
        const localSignatures = JSON.parse(localStorage.getItem('local_signatures') || '[]');
        console.log(`Loaded ${localSignatures.length} signatures from local storage`);
        return localSignatures;
    }
    
    async uploadSignature(signatureData) {
        try {
            const response = await fetch('/api/signatures/create', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(signatureData)
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                this.removeFromLocalStorage(signatureData.local_id);
                return true;
            }
            
            return false;
        } catch (error) {
            console.error('Upload error:', error);
            return false;
        }
    }
    
    removeFromLocalStorage(local_id) {
        let localSignatures = JSON.parse(localStorage.getItem('local_signatures') || '[]');
        localSignatures = localSignatures.filter(s => s.local_id !== local_id);
        localStorage.setItem('local_signatures', JSON.stringify(localSignatures));
    }
    
    async syncSignatures() {
        const localSignatures = this.loadLocalSignatures();
        
        if (localSignatures.length === 0) {
            this.showAlert('✅ No signatures to sync', 'info');
            return;
        }
        
        if (!navigator.onLine) {
            this.showAlert('⚠️ Cannot sync while offline', 'warning');
            return;
        }
        
        try {
            const response = await fetch('/api/signatures/sync', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    signatures: localSignatures
                })
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                localStorage.setItem('local_signatures', '[]');
                this.showAlert(`✅ Synced ${result.synced.length} signature(s)`, 'success');
                await this.loadAllData();
            } else {
                this.showAlert('⚠️ Sync failed: ' + result.message, 'warning');
            }
        } catch (error) {
            console.error('Sync error:', error);
            this.showAlert('⚠️ Sync failed', 'warning');
        }
    }
    
    async loadAllData() {
        try {
            const response = await fetch('/api/signatures/list?include_thumbnails=true');
            const result = await response.json();
            
            if (result.status === 'success') {
                this.signatures = result.signatures;
                this.assignments = result.assignments;
                
                this.renderSignatureLibrary();
                this.renderAssignments();
                this.updateAssignmentAlert(result.assignment_status);
            }
        } catch (error) {
            console.error('Load error:', error);
        }
    }
    
    renderSignatureLibrary() {
        const container = document.getElementById('signatureLibrary');
        if (!container) return;
        
        if (this.signatures.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-state-icon">✍️</div>
                    <div>No Signatures Yet</div>
                    <p>Create your first signature to get started</p>
                    <button class="btn btn-primary" onclick="app.openCreateModal()">Create Signature</button>
                </div>
            `;
            return;
        }
        
        container.innerHTML = this.signatures.map(sig => `
            <div class="signature-card">
                <div class="signature-preview">
                    <img src="data:image/png;base64,${sig.thumbnail_base64}" alt="${sig.name}">
                </div>
                <div class="signature-name">${sig.name}</div>
                <div class="signature-meta">${sig.role || 'No role specified'}</div>
                <div class="signature-meta">📱 ${sig.device_name}</div>
                <div class="signature-actions">
                    <button class="btn btn-danger" onclick="app.deleteSignature('${sig.id}')">
                        🗑️ Delete
                    </button>
                </div>
            </div>
        `).join('');
    }
    
    renderAssignments() {
        const container = document.getElementById('assignmentContainer');
        if (!container) return;
        
        const locations = [
            {
                key: 'toris_certifying_officer',
                label: 'TORIS Certifying Officer',
                description: 'Signature between lines on TORIS certification sheet'
            },
            {
                key: 'pg13_certifying_official',
                label: 'PG-13 Certifying Official (Top)',
                description: 'Top signature on PG-13 above "Certifying Official & Date"'
            },
            {
                key: 'pg13_member',
                label: 'PG-13 Member Signature (Bottom)',
                description: 'Bottom signature on PG-13 above "FI MI Last Name"'
            }
        ];
        
        const assignedIds = Object.values(this.assignments).filter(v => v !== null);
        const hasDuplicates = assignedIds.length !== new Set(assignedIds).size;
        
        container.innerHTML = locations.map(loc => {
            const assignedId = this.assignments[loc.key];
            const isAssigned = assignedId !== null;
            const isDuplicate = hasDuplicates && assignedIds.filter(id => id === assignedId).length > 1;
            
            const boxClass = isDuplicate ? 'assignment-box duplicate-warning' : 
                           isAssigned ? 'assignment-box assigned' : 'assignment-box';
            
            return `
                <div class="${boxClass}">
                    <div class="assignment-label">
                        ${loc.label}
                        <span class="status-badge ${isAssigned ? 'assigned' : 'unassigned'}">
                            ${isAssigned ? '✓ Assigned' : 'Not Assigned'}
                        </span>
                    </div>
                    <p style="font-size: 13px; color: #666; margin: 5px 0 15px 0;">${loc.description}</p>
                    
                    ${isDuplicate ? '<div class="alert alert-warning" style="margin-bottom: 15px;">⚠️ Warning: This signature is also used for another location</div>' : ''}
                    
                    <div class="signature-selector">
                        ${this.renderSignatureOptions(loc.key)}
                    </div>
                </div>
            `;
        }).join('');
    }
    
    renderSignatureOptions(location) {
        const currentAssignment = this.assignments[location];
        const otherAssignments = Object.entries(this.assignments)
            .filter(([key, value]) => key !== location && value !== null)
            .map(([key, value]) => value);
        
        let options = [`
            <label class="signature-option ${currentAssignment === null ? 'selected' : ''}">
                <input type="radio" 
                       name="${location}" 
                       value="" 
                       ${currentAssignment === null ? 'checked' : ''}
                       onchange="app.assignSignature('${location}', null)">
                <div class="signature-option-info">
                    <div class="signature-option-name">No Signature</div>
                    <div class="signature-option-role">Leave blank</div>
                </div>
            </label>
        `];
        
        this.signatures.forEach(sig => {
            const isSelected = currentAssignment === sig.id;
            const isDisabled = otherAssignments.includes(sig.id);
            const optionClass = `signature-option ${isSelected ? 'selected' : ''} ${isDisabled ? 'disabled' : ''}`;
            
            options.push(`
                <label class="${optionClass}">
                    <input type="radio" 
                           name="${location}" 
                           value="${sig.id}" 
                           ${isSelected ? 'checked' : ''}
                           ${isDisabled ? 'disabled' : ''}
                           onchange="app.assignSignature('${location}', '${sig.id}')">
                    <div class="signature-option-preview">
                        <img src="data:image/png;base64,${sig.thumbnail_base64}" alt="${sig.name}">
                    </div>
                    <div class="signature-option-info">
                        <div class="signature-option-name">${sig.name}</div>
                        <div class="signature-option-role">${sig.role || 'No role'}</div>
                        ${isDisabled ? '<div style="color: #dc3545; font-size: 12px; margin-top: 4px;">Already used</div>' : ''}
                    </div>
                </label>
            `);
        });
        
        return options.join('');
    }
    
    async assignSignature(location, signatureId) {
        try {
            const response = await fetch('/api/signatures/assign', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    location: location,
                    signature_id: signatureId || null
                })
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                await this.loadAllData();
                this.showAlert('✅ ' + result.message, 'success');
            } else {
                this.showAlert('❌ ' + result.message, 'warning');
                await this.loadAllData();
            }
        } catch (error) {
            console.error('Assign error:', error);
            this.showAlert('⚠️ Assignment failed', 'warning');
        }
    }
    
    async autoAssign() {
        try {
            const response = await fetch('/api/signatures/auto-assign', {
                method: 'POST'
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                await this.loadAllData();
                this.showAlert('✅ ' + result.message, 'success');
            } else {
                this.showAlert('⚠️ ' + result.message, 'warning');
            }
        } catch (error) {
            console.error('Auto-assign error:', error);
            this.showAlert('⚠️ Auto-assign failed', 'warning');
        }
    }
    
    async deleteSignature(signatureId) {
        if (!confirm('Delete this signature? This will also clear any document assignments using it.')) {
            return;
        }
        
        try {
            const response = await fetch(`/api/signatures/delete/${signatureId}`, {
                method: 'DELETE'
            });
            
            const result = await response.json();
            
            if (result.status === 'success') {
                await this.loadAllData();
                this.showAlert('✅ Signature deleted', 'success');
            } else {
                this.showAlert('⚠️ Delete failed', 'warning');
            }
        } catch (error) {
            console.error('Delete error:', error);
            this.showAlert('⚠️ Delete failed', 'warning');
        }
    }
    
    updateAssignmentAlert(status) {
        const alert = document.getElementById('assignmentAlert');
        if (!alert) return;
        
        if (status.issues.length > 0) {
            alert.className = 'alert alert-warning';
            alert.style.display = 'block';
            alert.innerHTML = `
                <strong>⚠️ Attention Needed:</strong><br>
                ${status.issues.map(issue => `• ${issue}`).join('<br>')}
            `;
        } else if (Object.values(this.assignments).every(v => v !== null)) {
            alert.className = 'alert alert-success';
            alert.style.display = 'block';
            alert.innerHTML = '<strong>✅ All locations have signatures assigned</strong>';
        } else {
            alert.style.display = 'none';
        }
    }
    
    showAlert(message, type) {
        const alert = document.getElementById('assignmentAlert');
        if (!alert) return;
        
        alert.className = `alert alert-${type}`;
        alert.style.display = 'block';
        alert.textContent = message;
        
        setTimeout(() => {
            alert.style.display = 'none';
        }, 5000);
    }
    
    checkOnlineStatus() {
        if (navigator.onLine) {
            this.handleOnline();
        } else {
            this.handleOffline();
        }
    }
    
    handleOnline() {
        const indicator = document.getElementById('syncIndicator');
        const statusText = document.getElementById('syncStatusText');
        
        if (indicator) indicator.className = 'sync-indicator';
        if (statusText) statusText.textContent = 'Online';
        
        const localSigs = this.loadLocalSignatures();
        if (localSigs.length > 0) {
            this.syncSignatures();
        }
    }
    
    handleOffline() {
        const indicator = document.getElementById('syncIndicator');
        const statusText = document.getElementById('syncStatusText');
        
        if (indicator) indicator.className = 'sync-indicator offline';
        if (statusText) statusText.textContent = 'Offline';
    }
}

let app;
document.addEventListener('DOMContentLoaded', () => {
    console.log('DOM loaded, initializing SignatureManager...');
    app = new SignatureManager();
    console.log('SignatureManager initialized');
});
