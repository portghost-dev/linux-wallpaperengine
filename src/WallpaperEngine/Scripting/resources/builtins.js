globalThis.__intervals = Object.create(null);
globalThis.MediaPlaybackEvent = globalThis.MediaPlaybackEvent || {
  PLAYBACK_STOPPED: 0,
  PLAYBACK_PLAYING: 1,
  PLAYBACK_PAUSED: 2
};
// Wallpaper Engine exposes Vec2/Vec3/Vec4 value types to scripts
// (e.g. `new Vec3(255, 192, 128)` fed to WEColor.normalizeColor).
// Plain x/y/z/w carriers; the C++ value bridge reads those fields.
// Arithmetic accepts a scalar or a same-arity vector (WE script API); every op
// returns a NEW vector. Required by e.g. the 3D-camera script (Gariam), which
// chains subtract/divide/multiply/add on camera transforms.
globalThis.Vec2 = globalThis.Vec2 || class Vec2 {
  constructor(x, y) { this.x = +x || 0; this.y = y === undefined ? this.x : (+y || 0); }
  copy() { return new Vec2(this.x, this.y); }
  add(o) { return typeof o === 'number' ? new Vec2(this.x + o, this.y + o) : new Vec2(this.x + o.x, this.y + o.y); }
  subtract(o) { return typeof o === 'number' ? new Vec2(this.x - o, this.y - o) : new Vec2(this.x - o.x, this.y - o.y); }
  multiply(o) { return typeof o === 'number' ? new Vec2(this.x * o, this.y * o) : new Vec2(this.x * o.x, this.y * o.y); }
  divide(o) { return typeof o === 'number' ? new Vec2(this.x / o, this.y / o) : new Vec2(this.x / o.x, this.y / o.y); }
  length() { return Math.hypot(this.x, this.y); }
  normalize() { const l = Math.hypot(this.x, this.y) || 1; return new Vec2(this.x / l, this.y / l); }
};
globalThis.Vec3 = globalThis.Vec3 || class Vec3 {
  constructor(x, y, z) {
    this.x = +x || 0;
    this.y = y === undefined ? this.x : (+y || 0);
    this.z = z === undefined ? this.x : (+z || 0);
  }
  copy() { return new Vec3(this.x, this.y, this.z); }
  add(o) { return typeof o === 'number' ? new Vec3(this.x + o, this.y + o, this.z + o) : new Vec3(this.x + o.x, this.y + o.y, this.z + o.z); }
  subtract(o) { return typeof o === 'number' ? new Vec3(this.x - o, this.y - o, this.z - o) : new Vec3(this.x - o.x, this.y - o.y, this.z - o.z); }
  multiply(o) { return typeof o === 'number' ? new Vec3(this.x * o, this.y * o, this.z * o) : new Vec3(this.x * o.x, this.y * o.y, this.z * o.z); }
  divide(o) { return typeof o === 'number' ? new Vec3(this.x / o, this.y / o, this.z / o) : new Vec3(this.x / o.x, this.y / o.y, this.z / o.z); }
  length() { return Math.hypot(this.x, this.y, this.z); }
  normalize() { const l = Math.hypot(this.x, this.y, this.z) || 1; return new Vec3(this.x / l, this.y / l, this.z / l); }
};
globalThis.Vec4 = globalThis.Vec4 || class Vec4 {
  constructor(x, y, z, w) { this.x = +x || 0; this.y = +y || 0; this.z = +z || 0; this.w = +w || 0; }
  copy() { return new Vec4(this.x, this.y, this.z, this.w); }
};

// WEMath/WEVector fallbacks, Mat4/Mat3, and an in-memory localStorage
// fallback. Every definition is `||`-guarded so a native C++ global wins.
(function () {
  'use strict';

  var DEG2RAD = Math.PI / 180;
  var RAD2DEG = 180 / Math.PI;
  var EPS = 1e-6;

  // Add a method to a prototype only if it does not already exist (so the
  // existing C++ implementations are never overridden).
  function addMethod(proto, name, fn) {
    if (proto && typeof proto[name] !== 'function') {
      Object.defineProperty(proto, name, {
        value: fn,
        writable: true,
        enumerable: false,
        configurable: true
      });
    }
  }

  function clampNum(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }
  function fractNum(v) { return v - Math.floor(v); }
  function modNum(x, y) { return x - y * Math.floor(x / y); }
  function smoothStepNum(edge0, edge1, x) {
    var t = clampNum((x - edge0) / (edge1 - edge0), 0, 1);
    return t * t * (3 - 2 * t);
  }

  globalThis.WEMath = globalThis.WEMath || {
    smoothStep: smoothStepNum,
    mix: function (a, b, t) { return a * (1 - t) + b * t; },
    // Constants, not converters: scripts use them as scalars (`angle * WEMath.rad2deg`,
    // `vec.multiply(WEMath.deg2rad)`). Matches MathModule's WEMath exports.
    deg2rad: DEG2RAD,
    rad2deg: RAD2DEG
  };

  globalThis.WEVector = globalThis.WEVector || {
    vectorAngle2: function (v) { return Math.atan2(v.y, v.x) * RAD2DEG; },
    angleVector2: function (angle) {
      var r = angle * DEG2RAD;
      return new Vec2(Math.cos(r), Math.sin(r));
    }
  };

  // ---- Vec2 -----------------------------------------------------------------
  if (typeof Vec2 !== 'undefined' && Vec2.prototype) {
    var P2 = Vec2.prototype;

    addMethod(P2, 'distance', function (other) {
      var dx = this.x - other.x, dy = this.y - other.y;
      return Math.sqrt(dx * dx + dy * dy);
    });
    addMethod(P2, 'distanceSqr', function (other) {
      var dx = this.x - other.x, dy = this.y - other.y;
      return dx * dx + dy * dy;
    });
    addMethod(P2, 'isFinite', function () {
      return Number.isFinite(this.x) && Number.isFinite(this.y);
    });
    addMethod(P2, 'negate', function () {
      return new Vec2(-this.x, -this.y);
    });
    addMethod(P2, 'reflect', function (normal) {
      var d = this.x * normal.x + this.y * normal.y;
      return new Vec2(this.x - 2 * d * normal.x, this.y - 2 * d * normal.y);
    });
    addMethod(P2, 'perpendicular', function () {
      return new Vec2(-this.y, this.x);
    });
    addMethod(P2, 'project', function (value) {
      var lenSqr = value.x * value.x + value.y * value.y;
      if (lenSqr < EPS * EPS) return new Vec2(0, 0);
      var d = (this.x * value.x + this.y * value.y) / lenSqr;
      return new Vec2(value.x * d, value.y * d);
    });
    addMethod(P2, 'angle', function () {
      return Math.atan2(this.y, this.x) * RAD2DEG;
    });
    addMethod(P2, 'angleBetween', function (value) {
      // Signed angle from this to value, in degrees.
      var dot = this.x * value.x + this.y * value.y;
      var det = this.x * value.y - this.y * value.x;
      return Math.atan2(det, dot) * RAD2DEG;
    });
    addMethod(P2, 'rotate', function (angle) {
      var r = angle * DEG2RAD, c = Math.cos(r), s = Math.sin(r);
      return new Vec2(this.x * c - this.y * s, this.x * s + this.y * c);
    });
    addMethod(P2, 'clamp', function (min, max) {
      var minX, minY, maxX, maxY;
      if (typeof min === 'number') { minX = min; minY = min; } else { minX = min.x; minY = min.y; }
      if (typeof max === 'number') { maxX = max; maxY = max; } else { maxX = max.x; maxY = max.y; }
      return new Vec2(clampNum(this.x, minX, maxX), clampNum(this.y, minY, maxY));
    });
    addMethod(P2, 'fract', function () {
      return new Vec2(fractNum(this.x), fractNum(this.y));
    });
    addMethod(P2, 'mod', function (value) {
      var vx, vy;
      if (typeof value === 'number') { vx = value; vy = value; } else { vx = value.x; vy = value.y; }
      return new Vec2(modNum(this.x, vx), modNum(this.y, vy));
    });
    addMethod(P2, 'step', function (edge) {
      var ex, ey;
      if (typeof edge === 'number') { ex = edge; ey = edge; } else { ex = edge.x; ey = edge.y; }
      return new Vec2(this.x < ex ? 0 : 1, this.y < ey ? 0 : 1);
    });
    addMethod(P2, 'smoothStep', function (min, max) {
      var minX, minY, maxX, maxY;
      if (typeof min === 'number') { minX = min; minY = min; } else { minX = min.x; minY = min.y; }
      if (typeof max === 'number') { maxX = max; maxY = max; } else { maxX = max.x; maxY = max.y; }
      return new Vec2(smoothStepNum(minX, maxX, this.x), smoothStepNum(minY, maxY, this.y));
    });
  }

  // ---- Vec3 -----------------------------------------------------------------
  if (typeof Vec3 !== 'undefined' && Vec3.prototype) {
    var P3 = Vec3.prototype;

    addMethod(P3, 'distance', function (other) {
      var dx = this.x - other.x, dy = this.y - other.y, dz = this.z - other.z;
      return Math.sqrt(dx * dx + dy * dy + dz * dz);
    });
    addMethod(P3, 'distanceSqr', function (other) {
      var dx = this.x - other.x, dy = this.y - other.y, dz = this.z - other.z;
      return dx * dx + dy * dy + dz * dz;
    });
    addMethod(P3, 'isFinite', function () {
      return Number.isFinite(this.x) && Number.isFinite(this.y) && Number.isFinite(this.z);
    });
    addMethod(P3, 'negate', function () {
      return new Vec3(-this.x, -this.y, -this.z);
    });
    addMethod(P3, 'reflect', function (normal) {
      var d = this.x * normal.x + this.y * normal.y + this.z * normal.z;
      return new Vec3(this.x - 2 * d * normal.x, this.y - 2 * d * normal.y, this.z - 2 * d * normal.z);
    });
    addMethod(P3, 'refract', function (normal, eta) {
      var dotNI = this.x * normal.x + this.y * normal.y + this.z * normal.z;
      var k = 1 - eta * eta * (1 - dotNI * dotNI);
      if (k < 0) return new Vec3(0, 0, 0);
      var f = eta * dotNI + Math.sqrt(k);
      return new Vec3(
        eta * this.x - f * normal.x,
        eta * this.y - f * normal.y,
        eta * this.z - f * normal.z
      );
    });
    addMethod(P3, 'project', function (value) {
      var lenSqr = value.x * value.x + value.y * value.y + value.z * value.z;
      if (lenSqr < EPS * EPS) return new Vec3(0, 0, 0);
      var d = (this.x * value.x + this.y * value.y + this.z * value.z) / lenSqr;
      return new Vec3(value.x * d, value.y * d, value.z * d);
    });
    addMethod(P3, 'angleBetween', function (value) {
      // Unsigned angle, in degrees.
      var lenA = Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z);
      var lenB = Math.sqrt(value.x * value.x + value.y * value.y + value.z * value.z);
      if (lenA < EPS || lenB < EPS) return 0;
      var c = (this.x * value.x + this.y * value.y + this.z * value.z) / (lenA * lenB);
      return Math.acos(clampNum(c, -1, 1)) * RAD2DEG;
    });
    addMethod(P3, 'toSpherical', function () {
      // Returns (r, theta, phi): theta = polar angle from +Y, phi = azimuth around Y.
      var r = Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z);
      if (r < EPS) return new Vec3(0, 0, 0);
      var theta = Math.acos(clampNum(this.y / r, -1, 1)) * RAD2DEG;
      var phi = Math.atan2(this.z, this.x) * RAD2DEG;
      return new Vec3(r, theta, phi);
    });
    addMethod(P3, 'clamp', function (min, max) {
      var minX, minY, minZ, maxX, maxY, maxZ;
      if (typeof min === 'number') { minX = min; minY = min; minZ = min; }
      else { minX = min.x; minY = min.y; minZ = min.z; }
      if (typeof max === 'number') { maxX = max; maxY = max; maxZ = max; }
      else { maxX = max.x; maxY = max.y; maxZ = max.z; }
      return new Vec3(clampNum(this.x, minX, maxX), clampNum(this.y, minY, maxY), clampNum(this.z, minZ, maxZ));
    });
    addMethod(P3, 'fract', function () {
      return new Vec3(fractNum(this.x), fractNum(this.y), fractNum(this.z));
    });
    addMethod(P3, 'mod', function (value) {
      var vx, vy, vz;
      if (typeof value === 'number') { vx = value; vy = value; vz = value; }
      else { vx = value.x; vy = value.y; vz = value.z; }
      return new Vec3(modNum(this.x, vx), modNum(this.y, vy), modNum(this.z, vz));
    });
    addMethod(P3, 'step', function (edge) {
      var ex, ey, ez;
      if (typeof edge === 'number') { ex = edge; ey = edge; ez = edge; }
      else { ex = edge.x; ey = edge.y; ez = edge.z; }
      return new Vec3(this.x < ex ? 0 : 1, this.y < ey ? 0 : 1, this.z < ez ? 0 : 1);
    });
    addMethod(P3, 'smoothStep', function (min, max) {
      var minX, minY, minZ, maxX, maxY, maxZ;
      if (typeof min === 'number') { minX = min; minY = min; minZ = min; }
      else { minX = min.x; minY = min.y; minZ = min.z; }
      if (typeof max === 'number') { maxX = max; maxY = max; maxZ = max; }
      else { maxX = max.x; maxY = max.y; maxZ = max.z; }
      return new Vec3(
        smoothStepNum(minX, maxX, this.x),
        smoothStepNum(minY, maxY, this.y),
        smoothStepNum(minZ, maxZ, this.z)
      );
    });

    // Static factory.
    if (typeof Vec3.fromSpherical !== 'function') {
      Vec3.fromSpherical = function (r, theta, phi) {
        var t = theta * DEG2RAD, p = phi * DEG2RAD;
        var st = Math.sin(t);
        return new Vec3(r * st * Math.cos(p), r * Math.cos(t), r * st * Math.sin(p));
      };
    }
  }

  // ---- Vec4 -----------------------------------------------------------------
  if (typeof Vec4 !== 'undefined' && Vec4.prototype) {
    var P4 = Vec4.prototype;

    addMethod(P4, 'distance', function (other) {
      var dx = this.x - other.x, dy = this.y - other.y, dz = this.z - other.z, dw = this.w - other.w;
      return Math.sqrt(dx * dx + dy * dy + dz * dz + dw * dw);
    });
    addMethod(P4, 'distanceSqr', function (other) {
      var dx = this.x - other.x, dy = this.y - other.y, dz = this.z - other.z, dw = this.w - other.w;
      return dx * dx + dy * dy + dz * dz + dw * dw;
    });
    addMethod(P4, 'isFinite', function () {
      return Number.isFinite(this.x) && Number.isFinite(this.y) &&
             Number.isFinite(this.z) && Number.isFinite(this.w);
    });
    addMethod(P4, 'negate', function () {
      return new Vec4(-this.x, -this.y, -this.z, -this.w);
    });
    addMethod(P4, 'reflect', function (normal) {
      var d = this.x * normal.x + this.y * normal.y + this.z * normal.z + this.w * normal.w;
      return new Vec4(
        this.x - 2 * d * normal.x,
        this.y - 2 * d * normal.y,
        this.z - 2 * d * normal.z,
        this.w - 2 * d * normal.w
      );
    });
    addMethod(P4, 'project', function (value) {
      var lenSqr = value.x * value.x + value.y * value.y + value.z * value.z + value.w * value.w;
      if (lenSqr < EPS * EPS) return new Vec4(0, 0, 0, 0);
      var d = (this.x * value.x + this.y * value.y + this.z * value.z + this.w * value.w) / lenSqr;
      return new Vec4(value.x * d, value.y * d, value.z * d, value.w * d);
    });
    addMethod(P4, 'clamp', function (min, max) {
      var minX, minY, minZ, minW, maxX, maxY, maxZ, maxW;
      if (typeof min === 'number') { minX = min; minY = min; minZ = min; minW = min; }
      else { minX = min.x; minY = min.y; minZ = min.z; minW = min.w; }
      if (typeof max === 'number') { maxX = max; maxY = max; maxZ = max; maxW = max; }
      else { maxX = max.x; maxY = max.y; maxZ = max.z; maxW = max.w; }
      return new Vec4(
        clampNum(this.x, minX, maxX), clampNum(this.y, minY, maxY),
        clampNum(this.z, minZ, maxZ), clampNum(this.w, minW, maxW)
      );
    });
    addMethod(P4, 'fract', function () {
      return new Vec4(fractNum(this.x), fractNum(this.y), fractNum(this.z), fractNum(this.w));
    });
    addMethod(P4, 'mod', function (value) {
      var vx, vy, vz, vw;
      if (typeof value === 'number') { vx = value; vy = value; vz = value; vw = value; }
      else { vx = value.x; vy = value.y; vz = value.z; vw = value.w; }
      return new Vec4(modNum(this.x, vx), modNum(this.y, vy), modNum(this.z, vz), modNum(this.w, vw));
    });
    addMethod(P4, 'step', function (edge) {
      var ex, ey, ez, ew;
      if (typeof edge === 'number') { ex = edge; ey = edge; ez = edge; ew = edge; }
      else { ex = edge.x; ey = edge.y; ez = edge.z; ew = edge.w; }
      return new Vec4(this.x < ex ? 0 : 1, this.y < ey ? 0 : 1, this.z < ez ? 0 : 1, this.w < ew ? 0 : 1);
    });
    addMethod(P4, 'smoothStep', function (min, max) {
      var minX, minY, minZ, minW, maxX, maxY, maxZ, maxW;
      if (typeof min === 'number') { minX = min; minY = min; minZ = min; minW = min; }
      else { minX = min.x; minY = min.y; minZ = min.z; minW = min.w; }
      if (typeof max === 'number') { maxX = max; maxY = max; maxZ = max; maxW = max; }
      else { maxX = max.x; maxY = max.y; maxZ = max.z; maxW = max.w; }
      return new Vec4(
        smoothStepNum(minX, maxX, this.x), smoothStepNum(minY, maxY, this.y),
        smoothStepNum(minZ, maxZ, this.z), smoothStepNum(minW, maxW, this.w)
      );
    });
  }

  // ===========================================================================
  // Mat3 / Mat4 - column-major storage (glm/GLSL convention).
  // For Mat4, m[col*4 + row]; for Mat3, m[col*3 + row].
  // ===========================================================================

  // ---- Mat4 -----------------------------------------------------------------
  function Mat4(src) {
    if (src && src.length === 16) {
      this.m = src.slice();
    } else {
      this.m = [
        1, 0, 0, 0,
        0, 1, 0, 0,
        0, 0, 1, 0,
        0, 0, 0, 1
      ];
    }
  }

  Mat4.identity = function () { return new Mat4(); };

  Mat4.fromTranslation = function (v) {
    var x = v.x, y = v.y, z = (typeof v.z === 'number' ? v.z : 0);
    var r = new Mat4();
    r.m[12] = x; r.m[13] = y; r.m[14] = z;
    return r;
  };

  Mat4.fromScale = function (v) {
    var sx, sy, sz;
    if (typeof v === 'number') { sx = v; sy = v; sz = v; }
    else { sx = v.x; sy = v.y; sz = (typeof v.z === 'number' ? v.z : 1); }
    var r = new Mat4();
    r.m[0] = sx; r.m[5] = sy; r.m[10] = sz;
    return r;
  };

  Mat4.fromRotation = function (angle, axis) {
    var rad = angle * DEG2RAD;
    var len = Math.sqrt(axis.x * axis.x + axis.y * axis.y + axis.z * axis.z);
    if (len < EPS) return new Mat4();
    var x = axis.x / len, y = axis.y / len, z = axis.z / len;
    var c = Math.cos(rad), s = Math.sin(rad), t = 1 - c;
    var r = new Mat4();
    // Column-major fill.
    r.m[0] = t * x * x + c;      r.m[1] = t * x * y + s * z;  r.m[2] = t * x * z - s * y;  r.m[3] = 0;
    r.m[4] = t * x * y - s * z;  r.m[5] = t * y * y + c;      r.m[6] = t * y * z + s * x;  r.m[7] = 0;
    r.m[8] = t * x * z + s * y;  r.m[9] = t * y * z - s * x;  r.m[10] = t * z * z + c;     r.m[11] = 0;
    r.m[12] = 0; r.m[13] = 0; r.m[14] = 0; r.m[15] = 1;
    return r;
  };

  Mat4.fromEuler = function (x, y, z) {
    var ax, ay, az;
    if (typeof x === 'object' && x !== null) { ax = x.x; ay = x.y; az = x.z; }
    else { ax = x; ay = y; az = z; }
    // Apply as Rz * Ry * Rx (intrinsic X then Y then Z).
    var rx = Mat4.fromRotation(ax, new Vec3(1, 0, 0));
    var ry = Mat4.fromRotation(ay, new Vec3(0, 1, 0));
    var rz = Mat4.fromRotation(az, new Vec3(0, 0, 1));
    return rz.multiply(ry).multiply(rx);
  };

  Mat4.fromBasis = function (right, up, forward) {
    var r = new Mat4();
    r.m[0] = right.x;   r.m[1] = right.y;   r.m[2] = right.z;   r.m[3] = 0;
    r.m[4] = up.x;      r.m[5] = up.y;      r.m[6] = up.z;      r.m[7] = 0;
    r.m[8] = forward.x; r.m[9] = forward.y; r.m[10] = forward.z; r.m[11] = 0;
    r.m[12] = 0; r.m[13] = 0; r.m[14] = 0; r.m[15] = 1;
    return r;
  };

  Mat4.lookAt = function (eye, center, up) {
    var fx = center.x - eye.x, fy = center.y - eye.y, fz = center.z - eye.z;
    var fl = Math.sqrt(fx * fx + fy * fy + fz * fz);
    if (fl < EPS) fl = 1;
    fx /= fl; fy /= fl; fz /= fl;
    // s = normalize(cross(f, up))
    var sx = fy * up.z - fz * up.y;
    var sy = fz * up.x - fx * up.z;
    var sz = fx * up.y - fy * up.x;
    var sl = Math.sqrt(sx * sx + sy * sy + sz * sz);
    if (sl < EPS) sl = 1;
    sx /= sl; sy /= sl; sz /= sl;
    // u = cross(s, f)
    var ux = sy * fz - sz * fy;
    var uy = sz * fx - sx * fz;
    var uz = sx * fy - sy * fx;
    var r = new Mat4();
    r.m[0] = sx;  r.m[1] = ux;  r.m[2] = -fx;  r.m[3] = 0;
    r.m[4] = sy;  r.m[5] = uy;  r.m[6] = -fy;  r.m[7] = 0;
    r.m[8] = sz;  r.m[9] = uz;  r.m[10] = -fz; r.m[11] = 0;
    r.m[12] = -(sx * eye.x + sy * eye.y + sz * eye.z);
    r.m[13] = -(ux * eye.x + uy * eye.y + uz * eye.z);
    r.m[14] = (fx * eye.x + fy * eye.y + fz * eye.z);
    r.m[15] = 1;
    return r;
  };

  Mat4.compose = function (translation, rotation, scale) {
    var t = Mat4.fromTranslation(translation);
    var r = Mat4.fromEuler(rotation);
    var s = Mat4.fromScale(scale);
    return t.multiply(r).multiply(s);
  };

  Mat4.prototype.translation = function (position) {
    if (position !== undefined && position !== null) {
      this.m[12] = position.x;
      this.m[13] = position.y;
      this.m[14] = (typeof position.z === 'number' ? position.z : 0);
    }
    return new Vec3(this.m[12], this.m[13], this.m[14]);
  };

  Mat4.prototype.right = function () { return new Vec3(this.m[0], this.m[1], this.m[2]); };
  Mat4.prototype.up = function () { return new Vec3(this.m[4], this.m[5], this.m[6]); };
  Mat4.prototype.forward = function () { return new Vec3(this.m[8], this.m[9], this.m[10]); };

  Mat4.prototype.add = function (other) {
    var r = new Mat4();
    for (var i = 0; i < 16; i++) r.m[i] = this.m[i] + other.m[i];
    return r;
  };
  Mat4.prototype.subtract = function (other) {
    var r = new Mat4();
    for (var i = 0; i < 16; i++) r.m[i] = this.m[i] - other.m[i];
    return r;
  };

  Mat4.prototype.multiply = function (value) {
    if (typeof value === 'number') {
      var rs = new Mat4();
      for (var i = 0; i < 16; i++) rs.m[i] = this.m[i] * value;
      return rs;
    }
    if (value instanceof Mat4) {
      var a = this.m, b = value.m, r = new Mat4();
      // r = this * value, column-major.
      for (var col = 0; col < 4; col++) {
        for (var row = 0; row < 4; row++) {
          r.m[col * 4 + row] =
            a[0 * 4 + row] * b[col * 4 + 0] +
            a[1 * 4 + row] * b[col * 4 + 1] +
            a[2 * 4 + row] * b[col * 4 + 2] +
            a[3 * 4 + row] * b[col * 4 + 3];
        }
      }
      return r;
    }
    // Treat as Vec4 -> returns Vec4.
    var m = this.m;
    var vx = value.x, vy = value.y, vz = value.z, vw = (typeof value.w === 'number' ? value.w : 1);
    return new Vec4(
      m[0] * vx + m[4] * vy + m[8] * vz + m[12] * vw,
      m[1] * vx + m[5] * vy + m[9] * vz + m[13] * vw,
      m[2] * vx + m[6] * vy + m[10] * vz + m[14] * vw,
      m[3] * vx + m[7] * vy + m[11] * vz + m[15] * vw
    );
  };

  Mat4.prototype.translate = function (v) { return this.multiply(Mat4.fromTranslation(v)); };
  Mat4.prototype.rotate = function (angle, axis) { return this.multiply(Mat4.fromRotation(angle, axis)); };
  Mat4.prototype.scale = function (v) { return this.multiply(Mat4.fromScale(v)); };

  Mat4.prototype.transformPoint = function (v) {
    var m = this.m;
    var vx = v.x, vy = v.y, vz = (typeof v.z === 'number' ? v.z : 0);
    var x = m[0] * vx + m[4] * vy + m[8] * vz + m[12];
    var y = m[1] * vx + m[5] * vy + m[9] * vz + m[13];
    var z = m[2] * vx + m[6] * vy + m[10] * vz + m[14];
    var w = m[3] * vx + m[7] * vy + m[11] * vz + m[15];
    if (w !== 0 && w !== 1) { x /= w; y /= w; z /= w; }
    return new Vec3(x, y, z);
  };

  Mat4.prototype.transformDirection = function (v) {
    var m = this.m;
    var vx = v.x, vy = v.y, vz = (typeof v.z === 'number' ? v.z : 0);
    return new Vec3(
      m[0] * vx + m[4] * vy + m[8] * vz,
      m[1] * vx + m[5] * vy + m[9] * vz,
      m[2] * vx + m[6] * vy + m[10] * vz
    );
  };

  Mat4.prototype.transpose = function () {
    var m = this.m, r = new Mat4();
    for (var col = 0; col < 4; col++)
      for (var row = 0; row < 4; row++)
        r.m[col * 4 + row] = m[row * 4 + col];
    return r;
  };

  Mat4.prototype.determinant = function () {
    var m = this.m;
    var a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3];
    var a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7];
    var a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11];
    var a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15];
    var b00 = a00 * a11 - a01 * a10;
    var b01 = a00 * a12 - a02 * a10;
    var b02 = a00 * a13 - a03 * a10;
    var b03 = a01 * a12 - a02 * a11;
    var b04 = a01 * a13 - a03 * a11;
    var b05 = a02 * a13 - a03 * a12;
    var b06 = a20 * a31 - a21 * a30;
    var b07 = a20 * a32 - a22 * a30;
    var b08 = a20 * a33 - a23 * a30;
    var b09 = a21 * a32 - a22 * a31;
    var b10 = a21 * a33 - a23 * a31;
    var b11 = a22 * a33 - a23 * a32;
    return b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
  };

  Mat4.prototype.inverse = function () {
    var m = this.m;
    var a00 = m[0], a01 = m[1], a02 = m[2], a03 = m[3];
    var a10 = m[4], a11 = m[5], a12 = m[6], a13 = m[7];
    var a20 = m[8], a21 = m[9], a22 = m[10], a23 = m[11];
    var a30 = m[12], a31 = m[13], a32 = m[14], a33 = m[15];
    var b00 = a00 * a11 - a01 * a10;
    var b01 = a00 * a12 - a02 * a10;
    var b02 = a00 * a13 - a03 * a10;
    var b03 = a01 * a12 - a02 * a11;
    var b04 = a01 * a13 - a03 * a11;
    var b05 = a02 * a13 - a03 * a12;
    var b06 = a20 * a31 - a21 * a30;
    var b07 = a20 * a32 - a22 * a30;
    var b08 = a20 * a33 - a23 * a30;
    var b09 = a21 * a32 - a22 * a31;
    var b10 = a21 * a33 - a23 * a31;
    var b11 = a22 * a33 - a23 * a32;
    var det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    if (Math.abs(det) < 1e-12) return new Mat4();
    var id = 1 / det;
    var r = new Mat4();
    r.m[0] = (a11 * b11 - a12 * b10 + a13 * b09) * id;
    r.m[1] = (a02 * b10 - a01 * b11 - a03 * b09) * id;
    r.m[2] = (a31 * b05 - a32 * b04 + a33 * b03) * id;
    r.m[3] = (a22 * b04 - a21 * b05 - a23 * b03) * id;
    r.m[4] = (a12 * b08 - a10 * b11 - a13 * b07) * id;
    r.m[5] = (a00 * b11 - a02 * b08 + a03 * b07) * id;
    r.m[6] = (a32 * b02 - a30 * b05 - a33 * b01) * id;
    r.m[7] = (a20 * b05 - a22 * b02 + a23 * b01) * id;
    r.m[8] = (a10 * b10 - a11 * b08 + a13 * b06) * id;
    r.m[9] = (a01 * b08 - a00 * b10 - a03 * b06) * id;
    r.m[10] = (a30 * b04 - a31 * b02 + a33 * b00) * id;
    r.m[11] = (a21 * b02 - a20 * b04 - a23 * b00) * id;
    r.m[12] = (a11 * b07 - a10 * b09 - a12 * b06) * id;
    r.m[13] = (a00 * b09 - a01 * b07 + a02 * b06) * id;
    r.m[14] = (a31 * b01 - a30 * b03 - a32 * b00) * id;
    r.m[15] = (a20 * b03 - a21 * b01 + a22 * b00) * id;
    return r;
  };

  Mat4.prototype.extractEuler = function () {
    var d = this.decompose();
    return d.rotation;
  };

  Mat4.prototype.normalMatrix = function () {
    return Mat3.fromMat4(this).inverse().transpose();
  };

  Mat4.prototype.decompose = function () {
    var m = this.m;
    var tx = m[12], ty = m[13], tz = m[14];
    // Column lengths give scale.
    var sx = Math.sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2]);
    var sy = Math.sqrt(m[4] * m[4] + m[5] * m[5] + m[6] * m[6]);
    var sz = Math.sqrt(m[8] * m[8] + m[9] * m[9] + m[10] * m[10]);
    // Handle negative determinant (mirroring) by flipping x scale.
    if (this.determinant() < 0) sx = -sx;
    var isx = sx !== 0 ? 1 / sx : 0;
    var isy = sy !== 0 ? 1 / sy : 0;
    var isz = sz !== 0 ? 1 / sz : 0;
    // Normalized rotation columns.
    var r00 = m[0] * isx, r01 = m[4] * isy, r02 = m[8] * isz;
    var r10 = m[1] * isx, r11 = m[5] * isy, r12 = m[9] * isz;
    var r20 = m[2] * isx, r21 = m[6] * isy, r22 = m[10] * isz;
    // Extract Euler (Rz * Ry * Rx order, intrinsic X-Y-Z).
    var ey = Math.asin(clampNum(-r20, -1, 1));
    var ex, ez;
    if (Math.abs(r20) < 1 - EPS) {
      ex = Math.atan2(r21, r22);
      ez = Math.atan2(r10, r00);
    } else {
      ex = Math.atan2(-r12, r11);
      ez = 0;
    }
    return {
      translation: new Vec3(tx, ty, tz),
      rotation: new Vec3(ex * RAD2DEG, ey * RAD2DEG, ez * RAD2DEG),
      scale: new Vec3(sx, sy, sz)
    };
  };

  Mat4.prototype.copy = function () { return new Mat4(this.m); };

  Mat4.prototype.equals = function (other) {
    for (var i = 0; i < 16; i++)
      if (Math.abs(this.m[i] - other.m[i]) > EPS) return false;
    return true;
  };

  Mat4.prototype.toString = function () {
    return 'mat4(' + this.m.join(', ') + ')';
  };

  // ---- Mat3 -----------------------------------------------------------------
  function Mat3(src) {
    if (src && src.length === 9) {
      this.m = src.slice();
    } else {
      this.m = [
        1, 0, 0,
        0, 1, 0,
        0, 0, 1
      ];
    }
  }

  Mat3.identity = function () { return new Mat3(); };

  Mat3.fromTranslation = function (v) {
    var r = new Mat3();
    r.m[6] = v.x; r.m[7] = v.y;
    return r;
  };

  Mat3.fromScale = function (v) {
    var sx, sy;
    if (typeof v === 'number') { sx = v; sy = v; }
    else { sx = v.x; sy = v.y; }
    var r = new Mat3();
    r.m[0] = sx; r.m[4] = sy;
    return r;
  };

  Mat3.fromRotation = function (angle) {
    var rad = angle * DEG2RAD, c = Math.cos(rad), s = Math.sin(rad);
    var r = new Mat3();
    r.m[0] = c;  r.m[1] = s;  r.m[2] = 0;
    r.m[3] = -s; r.m[4] = c;  r.m[5] = 0;
    r.m[6] = 0;  r.m[7] = 0;  r.m[8] = 1;
    return r;
  };

  Mat3.fromBasis = function (right, up) {
    var r = new Mat3();
    r.m[0] = right.x; r.m[1] = right.y; r.m[2] = 0;
    r.m[3] = up.x;    r.m[4] = up.y;    r.m[5] = 0;
    r.m[6] = 0;       r.m[7] = 0;       r.m[8] = 1;
    return r;
  };

  Mat3.fromMat4 = function (mat) {
    var s = mat.m, r = new Mat3();
    r.m[0] = s[0]; r.m[1] = s[1]; r.m[2] = s[2];
    r.m[3] = s[4]; r.m[4] = s[5]; r.m[5] = s[6];
    r.m[6] = s[8]; r.m[7] = s[9]; r.m[8] = s[10];
    return r;
  };

  Mat3.compose = function (translation, rotation, scale) {
    var t = Mat3.fromTranslation(translation);
    var r = Mat3.fromRotation(rotation);
    var s = Mat3.fromScale(scale);
    return t.multiply(r).multiply(s);
  };

  Mat3.prototype.translation = function (position) {
    if (position !== undefined && position !== null) {
      this.m[6] = position.x;
      this.m[7] = position.y;
    }
    return new Vec2(this.m[6], this.m[7]);
  };

  Mat3.prototype.angle = function () {
    // Forward angle from the right (red) basis column, in degrees.
    return Math.atan2(this.m[1], this.m[0]) * RAD2DEG;
  };

  Mat3.prototype.add = function (other) {
    var r = new Mat3();
    for (var i = 0; i < 9; i++) r.m[i] = this.m[i] + other.m[i];
    return r;
  };
  Mat3.prototype.subtract = function (other) {
    var r = new Mat3();
    for (var i = 0; i < 9; i++) r.m[i] = this.m[i] - other.m[i];
    return r;
  };

  Mat3.prototype.multiply = function (value) {
    if (typeof value === 'number') {
      var rs = new Mat3();
      for (var i = 0; i < 9; i++) rs.m[i] = this.m[i] * value;
      return rs;
    }
    if (value instanceof Mat3) {
      var a = this.m, b = value.m, r = new Mat3();
      for (var col = 0; col < 3; col++) {
        for (var row = 0; row < 3; row++) {
          r.m[col * 3 + row] =
            a[0 * 3 + row] * b[col * 3 + 0] +
            a[1 * 3 + row] * b[col * 3 + 1] +
            a[2 * 3 + row] * b[col * 3 + 2];
        }
      }
      return r;
    }
    // Treat as Vec3 -> returns Vec3.
    var m = this.m;
    var vx = value.x, vy = value.y, vz = (typeof value.z === 'number' ? value.z : 1);
    return new Vec3(
      m[0] * vx + m[3] * vy + m[6] * vz,
      m[1] * vx + m[4] * vy + m[7] * vz,
      m[2] * vx + m[5] * vy + m[8] * vz
    );
  };

  Mat3.prototype.translate = function (v) { return this.multiply(Mat3.fromTranslation(v)); };
  Mat3.prototype.rotate = function (angle) { return this.multiply(Mat3.fromRotation(angle)); };
  Mat3.prototype.scale = function (v) { return this.multiply(Mat3.fromScale(v)); };

  Mat3.prototype.transformPoint = function (v) {
    var m = this.m;
    var vx = v.x, vy = v.y;
    var x = m[0] * vx + m[3] * vy + m[6];
    var y = m[1] * vx + m[4] * vy + m[7];
    var w = m[2] * vx + m[5] * vy + m[8];
    if (w !== 0 && w !== 1) { x /= w; y /= w; }
    return new Vec2(x, y);
  };

  Mat3.prototype.transformDirection = function (v) {
    var m = this.m;
    var vx = v.x, vy = v.y;
    return new Vec2(m[0] * vx + m[3] * vy, m[1] * vx + m[4] * vy);
  };

  Mat3.prototype.transpose = function () {
    var m = this.m, r = new Mat3();
    for (var col = 0; col < 3; col++)
      for (var row = 0; row < 3; row++)
        r.m[col * 3 + row] = m[row * 3 + col];
    return r;
  };

  Mat3.prototype.determinant = function () {
    var m = this.m;
    return m[0] * (m[4] * m[8] - m[5] * m[7]) -
           m[3] * (m[1] * m[8] - m[2] * m[7]) +
           m[6] * (m[1] * m[5] - m[2] * m[4]);
  };

  Mat3.prototype.inverse = function () {
    var m = this.m;
    var a00 = m[0], a01 = m[1], a02 = m[2];
    var a10 = m[3], a11 = m[4], a12 = m[5];
    var a20 = m[6], a21 = m[7], a22 = m[8];
    var c00 = a11 * a22 - a12 * a21;
    var c01 = a12 * a20 - a10 * a22;
    var c02 = a10 * a21 - a11 * a20;
    var det = a00 * c00 + a01 * c01 + a02 * c02;
    if (Math.abs(det) < 1e-12) return new Mat3();
    var id = 1 / det;
    var r = new Mat3();
    r.m[0] = c00 * id;
    r.m[1] = (a02 * a21 - a01 * a22) * id;
    r.m[2] = (a01 * a12 - a02 * a11) * id;
    r.m[3] = c01 * id;
    r.m[4] = (a00 * a22 - a02 * a20) * id;
    r.m[5] = (a02 * a10 - a00 * a12) * id;
    r.m[6] = c02 * id;
    r.m[7] = (a01 * a20 - a00 * a21) * id;
    r.m[8] = (a00 * a11 - a01 * a10) * id;
    return r;
  };

  Mat3.prototype.decompose = function () {
    var m = this.m;
    var tx = m[6], ty = m[7];
    var sx = Math.sqrt(m[0] * m[0] + m[1] * m[1]);
    var sy = Math.sqrt(m[3] * m[3] + m[4] * m[4]);
    if (this.determinant() < 0) sx = -sx;
    var rotation = Math.atan2(m[1], m[0]) * RAD2DEG;
    return {
      translation: new Vec2(tx, ty),
      rotation: rotation,
      scale: new Vec2(sx, sy)
    };
  };

  Mat3.prototype.copy = function () { return new Mat3(this.m); };

  Mat3.prototype.equals = function (other) {
    for (var i = 0; i < 9; i++)
      if (Math.abs(this.m[i] - other.m[i]) > EPS) return false;
    return true;
  };

  Mat3.prototype.toString = function () {
    return 'mat3(' + this.m.join(', ') + ')';
  };

  globalThis.Mat4 = globalThis.Mat4 || Mat4;
  globalThis.Mat3 = globalThis.Mat3 || Mat3;

  // ===========================================================================
  // localStorage - in-memory, namespaced by location. Matches ILocalStorage.
  // ===========================================================================
  var LOCATION_GLOBAL = 'global';
  var LOCATION_SCREEN = 'screen';

  var localStorageImpl = {
    LOCATION_GLOBAL: LOCATION_GLOBAL,
    LOCATION_SCREEN: LOCATION_SCREEN,
    __data: Object.create(null),
    __bucket: function (location) {
      var loc = (location === undefined || location === null) ? LOCATION_GLOBAL : String(location);
      if (!Object.prototype.hasOwnProperty.call(this.__data, loc)) {
        this.__data[loc] = Object.create(null);
      }
      return this.__data[loc];
    },
    set: function (key, value, location) {
      this.__bucket(location)[String(key)] = String(value);
    },
    get: function (key, location) {
      var bucket = this.__bucket(location);
      key = String(key);
      return Object.prototype.hasOwnProperty.call(bucket, key) ? bucket[key] : null;
    },
    delete: function (key, location) {
      delete this.__bucket(location)[String(key)];
    },
    clear: function (location) {
      if (location === undefined || location === null) {
        this.__data = Object.create(null);
      } else {
        this.__data[String(location)] = Object.create(null);
      }
    }
  };
  // Backward-compat alias.
  localStorageImpl.remove = localStorageImpl.delete;

  globalThis.localStorage = globalThis.localStorage || localStorageImpl;
})();
