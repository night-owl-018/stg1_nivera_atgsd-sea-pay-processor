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

    // IMPORTANT: do NOT clone/replace the canvas node.
    // Replacing the node breaks coordinate mapping in iOS modals and can drop listeners.
    const parent = this.canvas.parentElement;
    const rect = parent.getBoundingClientRect();

    // CSS size (display)
    this._cssW = Math.max(300, Math.min(720, Math.round(rect.width)));
    this._cssH = 220;

    // Backing store size (for sharpness)
    this._dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));

    this.canvas.style.width = this._cssW + 'px';
    this.canvas.style.height = this._cssH + 'px';
    this.canvas.width = Math.round(this._cssW * this._dpr);
    this.canvas.height = Math.round(this._cssH * this._dpr);

    this.ctx = this.canvas.getContext('2d', { willReadFrequently: false });
    // Draw in CSS units by scaling the context to DPR
    this.ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);

    // Ink style
    this.ctx.strokeStyle = '#000';
    this.ctx.lineCap = 'round';
    this.ctx.lineJoin = 'round';
    this.ctx.miterLimit = 2;

    // Prevent scroll/zoom while signing
    this.canvas.style.touchAction = 'none';

    // Remove prior listeners (if any) then bind fresh ones
    this._unbindCanvasEvents();
    this._bindCanvasEvents();

    console.log(`Canvas initialized: CSS ${this._cssW}x${this._cssH}, DPR ${this._dpr}, backing ${this.canvas.width}x${this.canvas.height}`);
}

_bindCanvasEvents() {
    // Prefer Pointer Events (Safari + Brave on iOS support this via WebKit)
    this._onPointerDown = (e) => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        e.preventDefault();
        try { this.canvas.setPointerCapture(e.pointerId); } catch (_) {}
        this._strokeStart(this._eventToPoint(e));
    };

    this._onPointerMove = (e) => {
        if (!this.isDrawing) return;
        e.preventDefault();
        this._strokeMove(this._eventToPoint(e));
    };

    this._onPointerUp = (e) => {
        if (!this.isDrawing) return;
        e.preventDefault();
        this._strokeEnd();
    };

    this._onPointerCancel = (e) => {
        if (!this.isDrawing) return;
        e.preventDefault();
        this._strokeEnd();
    };

    if (window.PointerEvent) {
        this.canvas.addEventListener('pointerdown', this._onPointerDown, { passive: false });
        this.canvas.addEventListener('pointermove', this._onPointerMove, { passive: false });
        this.canvas.addEventListener('pointerup', this._onPointerUp, { passive: false });
        this.canvas.addEventListener('pointercancel', this._onPointerCancel, { passive: false });
    } else {
        // Fallback (older browsers): touch + mouse
        this._onTouchStart = (e) => {
            e.preventDefault();
            const t = e.touches[0];
            this._strokeStart(this._clientToPoint(t.clientX, t.clientY));
        };
        this._onTouchMove = (e) => {
            if (!this.isDrawing) return;
            e.preventDefault();
            const t = e.touches[0];
            this._strokeMove(this._clientToPoint(t.clientX, t.clientY));
        };
        this._onTouchEnd = (e) => {
            e.preventDefault();
            this._strokeEnd();
        };

        this.canvas.addEventListener('touchstart', this._onTouchStart, { passive: false });
        this.canvas.addEventListener('touchmove', this._onTouchMove, { passive: false });
        this.canvas.addEventListener('touchend', this._onTouchEnd, { passive: false });
        this.canvas.addEventListener('touchcancel', this._onTouchEnd, { passive: false });

        this._onMouseDown = (e) => { e.preventDefault(); this._strokeStart(this._clientToPoint(e.clientX, e.clientY)); };
        this._onMouseMove = (e) => { if (!this.isDrawing) return; e.preventDefault(); this._strokeMove(this._clientToPoint(e.clientX, e.clientY)); };
        this._onMouseUp = (e) => { e.preventDefault(); this._strokeEnd(); };

        this.canvas.addEventListener('mousedown', this._onMouseDown);
        window.addEventListener('mousemove', this._onMouseMove);
        window.addEventListener('mouseup', this._onMouseUp);
    }

    // Reflow/rotation can change modal geometry on iOS; rebuild mapping on resize.
    this._onResize = () => {
        if (!this.canvas || !this.ctx) return;
        this.setupCanvas();
        this.clearCanvas();
    };
    window.addEventListener('resize', this._onResize);
}

_unbindCanvasEvents() {
    if (!this.canvas) return;

    if (this._onPointerDown) {
        this.canvas.removeEventListener('pointerdown', this._onPointerDown);
        this.canvas.removeEventListener('pointermove', this._onPointerMove);
        this.canvas.removeEventListener('pointerup', this._onPointerUp);
        this.canvas.removeEventListener('pointercancel', this._onPointerCancel);
    }
    if (this._onTouchStart) {
        this.canvas.removeEventListener('touchstart', this._onTouchStart);
        this.canvas.removeEventListener('touchmove', this._onTouchMove);
        this.canvas.removeEventListener('touchend', this._onTouchEnd);
        this.canvas.removeEventListener('touchcancel', this._onTouchEnd);
    }
    if (this._onMouseDown) {
        this.canvas.removeEventListener('mousedown', this._onMouseDown);
        window.removeEventListener('mousemove', this._onMouseMove);
        window.removeEventListener('mouseup', this._onMouseUp);
    }
    if (this._onResize) {
        window.removeEventListener('resize', this._onResize);
    }

    this._onPointerDown = this._onPointerMove = this._onPointerUp = this._onPointerCancel = null;
    this._onTouchStart = this._onTouchMove = this._onTouchEnd = null;
    this._onMouseDown = this._onMouseMove = this._onMouseUp = null;
    this._onResize = null;
}

_eventToPoint(e) {
    return this._clientToPoint(e.clientX, e.clientY);
}

_clientToPoint(clientX, clientY) {
    // Always compute from current bounding rect (iOS modal can shift while open)
    const rect = this.canvas.getBoundingClientRect();
    return { x: clientX - rect.left, y: clientY - rect.top, t: performance.now() };
}

_strokeStart(p) {
    if (!this.ctx) return;
    this.isDrawing = true;
    this.points = [];

    this._lastPoint = { x: p.x, y: p.y };
    this._lastMid = { x: p.x, y: p.y };
    this._lastTs = p.t;
    this._lastWidth = 2.8;  // Initialize width smoothing for professional transitions

    this.points.push({ x: p.x, y: p.y });

    // tiny dot so taps register
    this.ctx.beginPath();
    this.ctx.lineWidth = 2.8;
    this.ctx.moveTo(p.x, p.y);
    this.ctx.lineTo(p.x + 0.01, p.y + 0.01);
    this.ctx.stroke();
}

_strokeMove(p) {
    if (!this.isDrawing || !this.ctx || !this._lastPoint) return;

    const now = p.t;
    const dx = p.x - this._lastPoint.x;
    const dy = p.y - this._lastPoint.y;
    const dist = Math.hypot(dx, dy);

    if (dist < 0.4) return;

    // interpolate to avoid sharp corners on fast moves
    const step = 2.0;
    const segments = Math.min(24, Math.max(1, Math.floor(dist / step)));

    for (let i = 1; i <= segments; i++) {
        const t = i / segments;
        const x = this._lastPoint.x + dx * t;
        const y = this._lastPoint.y + dy * t;
        const ts = this._lastTs + (now - this._lastTs) * t;
        this._drawSmoothPoint({ x, y, t: ts });
        this.points.push({ x, y });
    }

    this._lastPoint = { x: p.x, y: p.y };
    this._lastTs = now;
}

_drawSmoothPoint(p) {
    const mid = { x: (this._lastPoint.x + p.x) / 2, y: (this._lastPoint.y + p.y) / 2 };

    // Enhanced speed-based width for professional pen feel
    const dt = Math.max(8, p.t - this._lastTs);
    const vx = p.x - this._lastPoint.x;
    const vy = p.y - this._lastPoint.y;
    const v = Math.hypot(vx, vy) / dt; // px/ms

    // Professional signature line width (smoother transitions)
    const maxW = 3.8;  // Slightly reduced max for cleaner look
    const minW = 1.8;  // Slightly increased min for consistency
    const k = 4.0;     // Increased sensitivity for better variation
    
    // Smooth velocity-based width with exponential decay for natural feel
    const velocityFactor = Math.min(1.0, v * k / maxW);
    const w = maxW - (velocityFactor * (maxW - minW));
    
    // Apply smoothed width (prevents sudden jumps) - KEY IMPROVEMENT
    if (!this._lastWidth) this._lastWidth = w;
    const smoothW = this._lastWidth * 0.7 + w * 0.3;  // 70% old + 30% new = smooth transitions
    this._lastWidth = smoothW;

    this.ctx.lineWidth = smoothW;
    this.ctx.beginPath();
    this.ctx.moveTo(this._lastMid.x, this._lastMid.y);
    
    // Use quadratic curve for smooth professional appearance
    this.ctx.quadraticCurveTo(this._lastPoint.x, this._lastPoint.y, mid.x, mid.y);
    this.ctx.stroke();

    this._lastMid = mid;
}

_strokeEnd() {
    this.isDrawing = false;
    this._lastPoint = null;
    this._lastMid = null;
    this._lastTs = 0;
    this._lastWidth = null;  // Reset width smoothing for next stroke
    console.log('Drawing stopped, total points:', this.points.length);
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
    
    
    
    
    
    
    
clearCanvas() {
    if (!this.ctx || !this.canvas) {
        console.warn('Canvas not initialized, skipping clear');
        return;
    }
    // Clear in CSS units (ctx is scaled to DPR)
    this.ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);
    this.ctx.clearRect(0, 0, this._cssW || this.canvas.width, this._cssH || this.canvas.height);
    this.points = [];
    this.isDrawing = false;
    this._lastPoint = null;
    this._lastMid = null;
    this._lastTs = 0;
    this._lastWidth = null;  // Reset width smoothing
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

    // Detach canvas listeners to avoid duplicate bindings
    this._unbindCanvasEvents();

    // Clean up canvas reference
    this.canvas = null;
    this.ctx = null;
    this.points = [];
    this.isDrawing = false;
    this._lastPoint = null;
    this._lastMid = null;
    this._lastTs = 0;
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
