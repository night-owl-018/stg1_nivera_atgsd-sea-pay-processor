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
    const rect = parent ? parent.getBoundingClientRect() : this.canvas.getBoundingClientRect();

    // CSS display size
    this._cssW = Math.max(300, Math.min(720, Math.round(rect.width || 600)));
    this._cssH = 220;

    // PERFECT SETTINGS: Maximum resolution for ultra-smooth signatures
    // Force minimum 3x, allow up to 5x for extreme quality
    const deviceDPR = window.devicePixelRatio || 2;
    this._dpr = Math.max(3, Math.min(5, deviceDPR));  // 3x-5x = PERFECT quality

    this.canvas.style.width = this._cssW + 'px';
    this.canvas.style.height = this._cssH + 'px';
    this.canvas.width = Math.round(this._cssW * this._dpr);
    this.canvas.height = Math.round(this._cssH * this._dpr);

    // PERFECT: Advanced context options for maximum quality
    this.ctx = this.canvas.getContext('2d', { 
        alpha: true,
        desynchronized: false,        // Sync for quality over speed
        willReadFrequently: false,    // Optimize for drawing, not reading
        colorSpace: 'srgb'            // Standard color space
    });

    // Draw in CSS units, but render at ultra-high device resolution
    this.ctx.setTransform(this._dpr, 0, 0, this._dpr, 0, 0);

    // PERFECT: Professional ink rendering settings
    this.ctx.strokeStyle = '#000000';    // Pure black
    this.ctx.lineCap = 'round';          // Perfectly round line ends
    this.ctx.lineJoin = 'round';         // Perfectly round corners
    this.ctx.miterLimit = 10;            // High quality miters
    
    // PERFECT: Force maximum anti-aliasing
    this.ctx.imageSmoothingEnabled = true;
    this.ctx.imageSmoothingQuality = 'high';  // Maximum browser smoothing
    
    // PERFECT: Additional quality settings
    this.ctx.globalCompositeOperation = 'source-over';  // Standard blending
    this.ctx.globalAlpha = 1.0;                         // Full opacity
    
    // PERFECT: Subpixel rendering (critical for smoothness)
    this.ctx.translate(0.5, 0.5);  // Half-pixel offset for subpixel rendering
    
    // iOS: prevent scroll/zoom while signing
    this.canvas.style.touchAction = 'none';
    
    // PERFECT: Additional canvas styling for quality
    this.canvas.style.imageRendering = 'auto';  // Let browser use best rendering
    this.canvas.style.WebkitFontSmoothing = 'antialiased';

    // Reset stroke state
    this._stroke = {
        raw: null,          // last raw point (for resampling)
        pts: [],            // smoothed points (for curves)
        lastT: 0,
        lastW: 2.8
    };

    this._unbindCanvasEvents();
    this._bindCanvasEvents();

    console.log(`✨ PERFECT MODE: CSS ${this._cssW}x${this._cssH}, DPR ${this._dpr}x (${this.canvas.width}x${this.canvas.height}px)`);
}

_bindCanvasEvents() {
    // Pointer Events are the most reliable across Safari/Brave on iOS and desktop
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
        // Fallback (very old browsers)
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
        this._onTouchEnd = (e) => { e.preventDefault(); this._strokeEnd(); };

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
        if (!this.canvas) return;
        const existing = this.canvas.toDataURL('image/png'); // keep current stroke preview
        this.setupCanvas();
        this.clearCanvas();
        // (We don't redraw the existing image to avoid smoothing artifacts mid-stroke)
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

    this._stroke.raw = { x: p.x, y: p.y, t: p.t };
    this._stroke.pts = [];
    this._stroke.lastT = p.t;
    this._stroke.lastW = 2.8;

    // Seed points for curve engine
    const sp = { x: p.x, y: p.y, t: p.t, w: 2.8 };
    this._stroke.pts.push(sp, sp, sp, sp);
    this.points.push({ x: p.x, y: p.y });

    // dot for taps
    this.ctx.beginPath();
    this.ctx.lineWidth = 2.8;
    this.ctx.moveTo(p.x, p.y);
    this.ctx.lineTo(p.x + 0.01, p.y + 0.01);
    this.ctx.stroke();
}

_strokeMove(p) {
    if (!this.isDrawing || !this.ctx || !this._stroke.raw) return;

    const a = this._stroke.raw;
    const dx = p.x - a.x;
    const dy = p.y - a.y;
    const dist = Math.hypot(dx, dy);

    // Ignore micro jitter
    if (dist < 0.35) return;

    // Resample points so fast motion doesn't create corners.
// Key idea: when the pointer moves quickly (or curvature is high), sample much more densely.
const dt = Math.max(1, (p.t - a.t) || 1);                 // ms
const speed = dist / dt;                                  // px/ms

// Curvature hint: if direction changed sharply, we densify even more.
let turnBoost = 1.0;
const lp = this.points.length >= 2 ? this.points[this.points.length - 1] : null;
const lpp = this.points.length >= 3 ? this.points[this.points.length - 2] : null;
if (lp && lpp) {
    const v1x = lp.x - lpp.x, v1y = lp.y - lpp.y;
    const v2x = p.x - lp.x,  v2y = p.y - lp.y;
    const d1 = Math.hypot(v1x, v1y), d2 = Math.hypot(v2x, v2y);
    if (d1 > 0.001 && d2 > 0.001) {
        const cos = (v1x * v2x + v1y * v2y) / (d1 * d2);
        const angle = Math.acos(Math.max(-1, Math.min(1, cos))) * (180 / Math.PI);
        if (angle > 35) turnBoost = 1.35;
        if (angle > 70) turnBoost = 1.65;
    }
}

// Base step tightened to eliminate visible corners on circles/curves.
// We keep sampling dense even at slow speed; tighter turns get even denser.
const baseStep = 0.40;   // px
const minStep = 0.08;    // px (very dense)
// Speed reduces step; turnBoost reduces it further.
const step = Math.max(minStep, (baseStep - Math.min(0.30, speed * 0.50)) / turnBoost);
// Never under-sample long jumps.
const n = Math.max(1, Math.ceil(dist / step));


    for (let i = 1; i <= n; i++) {
        const t = i / n;
        const x = a.x + dx * t;
        const y = a.y + dy * t;
        const ts = a.t + (p.t - a.t) * t;
        this._addPoint({ x, y, t: ts });
        this.points.push({ x, y });
    }

    this._stroke.raw = { x: p.x, y: p.y, t: p.t };
}

_addPoint(p) {
    // PERFECT: Ultra-responsive pressure-sensitive pen simulation
    const dt = Math.max(8, p.t - this._stroke.lastT);
    const lp = this._stroke.pts[this._stroke.pts.length - 1];
    const v = Math.hypot(p.x - lp.x, p.y - lp.y) / dt; // px/ms

    // PERFECT: Maximum range for dramatic heavy/light variation
    const maxW = 5.0;  // Heavy pressure = very thick (was 4.5)
    const minW = 1.2;  // Light/fast = very thin (was 1.5)
    const k = 6.0;     // Maximum responsiveness (was 5.5)
    
    // ENHANCED: Power curve for natural pen feel (0.8 exponent)
    const vf = Math.min(1, Math.pow(v * k / maxW, 0.8));
    const wRaw = maxW - vf * (maxW - minW);

    // ENHANCED: More responsive width transitions (was 0.75/0.25, now 0.65/0.35)
    const w = this._stroke.lastW * 0.65 + wRaw * 0.35;
    this._stroke.lastW = w;
    this._stroke.lastT = p.t;

    const pt = { x: p.x, y: p.y, t: p.t, w };

    // Light position smoothing (reduces tiny kinks on curves without drifting)
    const prev = this._stroke.pts.length ? this._stroke.pts[this._stroke.pts.length - 1] : null;
    if (prev) {
        // Slightly stronger smoothing to remove micro-kinks (helps iPhone curves).
        pt.x = prev.x * 0.25 + pt.x * 0.75;
        pt.y = prev.y * 0.25 + pt.y * 0.75;
    }

    this._stroke.pts.push(pt);


    // Keep only what we need
    if (this._stroke.pts.length < 4) return;

    // Draw latest Catmull-Rom segment converted to Bezier:
    // Segment from P1 to P2 using P0,P1,P2,P3
    const pts = this._stroke.pts;
    const p0 = pts[pts.length - 4];
    const p1 = pts[pts.length - 3];
    const p2 = pts[pts.length - 2];
    const p3 = pts[pts.length - 1];

    // Catmull-Rom -> Bezier control points (adaptive tension).
// Classic /6 can still show corners when the pointer moves fast and points are sparse.
// We increase the divisor at higher velocity to damp sharp bends.
const vForTension = v; // px/ms from above
const denom = 12 + Math.min(20, vForTension * 60); // 12..32 (smoother control points)
const cp1 = {
    x: p1.x + (p2.x - p0.x) / denom,
    y: p1.y + (p2.y - p0.y) / denom
};
const cp2 = {
    x: p2.x - (p3.x - p1.x) / denom,
    y: p2.y - (p3.y - p1.y) / denom
};


    // Line width based on destination point (smooth enough with resampling)
    // Use averaged width for smoother segment joins.
    this.ctx.lineWidth = (p1.w + p2.w) / 2;
    
    // PERFECT: Maximum quality rendering for every stroke
    this.ctx.imageSmoothingEnabled = true;
    this.ctx.imageSmoothingQuality = 'high';
    this.ctx.lineCap = 'round';
    this.ctx.lineJoin = 'round';
    
    // PERFECT: Shadow for depth (very subtle, adds smoothness perception)
    this.ctx.shadowBlur = 0.3;
    this.ctx.shadowColor = 'rgba(0, 0, 0, 0.1)';
    this.ctx.shadowOffsetX = 0;
    this.ctx.shadowOffsetY = 0;
    
    this.ctx.beginPath();
    this.ctx.moveTo(p1.x, p1.y);
    this.ctx.bezierCurveTo(cp1.x, cp1.y, cp2.x, cp2.y, p2.x, p2.y);
    this.ctx.stroke();
}

_strokeEnd() {
    this.isDrawing = false;
    this._stroke.raw = null;
    this._stroke.pts = [];
    this._stroke.lastT = 0;
    this._stroke.lastW = 2.8;
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
    this.ctx.clearRect(0, 0, this._cssW || (this.canvas.width / this._dpr), this._cssH || (this.canvas.height / this._dpr));

    this.points = [];
    this.isDrawing = false;

    if (this._stroke) {
        this._stroke.raw = null;
        this._stroke.pts = [];
        this._stroke.lastT = 0;
        this._stroke.lastW = 2.8;
    }

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
