// Event-driven GPU drawing: full device pixel ratio without an idle animation loop.
export class RasterViewer {
  constructor(canvas, status) {
    this.canvas = canvas;
    this.status = status;
    this.zoom = 1;
    this.pan = [0, 0];
    this.images = {};
    this.textures = {};
    this.inputAspects = {};
    this.layer = 'height';
    this.overlay = true;
    this.frame = null;
    this.gl = canvas.getContext('webgl', { alpha: false, antialias: false, powerPreference: 'high-performance' });
    if (this.gl) {
      this.initGL();
      this.updateRendererStatus();
      canvas.addEventListener('webglcontextlost', event => {
        event.preventDefault();
        status.textContent = 'نمایشگر GPU متوقف شد؛ در انتظار بازیابی';
      });
      canvas.addEventListener('webglcontextrestored', () => {
        this.initGL();
        for (const [key, img] of Object.entries(this.images)) this.uploadTexture(key, img);
        this.updateRendererStatus();
        this.draw();
      });
    } else {
      this.ctx = canvas.getContext('2d', { alpha: false });
      status.textContent = 'Canvas · حالت سازگار';
    }
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement);
    canvas.addEventListener('wheel', event => {
      event.preventDefault();
      const rect = canvas.getBoundingClientRect();
      this.zoomBy(Math.exp(-event.deltaY * .0015), [event.clientX - rect.left, event.clientY - rect.top]);
    }, { passive: false });
    let drag = null;
    canvas.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY };
      canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      this.pan[0] += event.clientX - drag.x;
      this.pan[1] += event.clientY - drag.y;
      drag.x = event.clientX;
      drag.y = event.clientY;
      this.draw();
    });
    const release = () => { drag = null; };
    canvas.addEventListener('pointerup', release);
    canvas.addEventListener('pointercancel', release);
    canvas.addEventListener('lostpointercapture', release);
  }

  updateRendererStatus() {
    const extension = this.gl.getExtension('WEBGL_debug_renderer_info');
    const renderer = extension ? this.gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : '';
    const software = /swiftshader|llvmpipe|softpipe|software|lavapipe/i.test(renderer);
    this.status.textContent = software ? 'WebGL · رندر نرم‌افزاری' : 'WebGL · رندر گرافیکی';
  }

  initGL() {
    const gl = this.gl;
    const empty = document.createElement('canvas');
    empty.width = empty.height = 1;
    this.uploadTexture('empty', empty);
    const compile = (type, source) => {
      const shader = gl.createShader(type);
      gl.shaderSource(shader, source);
      gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const vertex = compile(gl.VERTEX_SHADER, `
      attribute vec2 a_position;
      varying vec2 v_position;
      void main() {
        v_position = vec2(a_position.x * .5 + .5, .5 - a_position.y * .5);
        gl_Position = vec4(a_position, 0., 1.);
      }`);
    const fragment = compile(gl.FRAGMENT_SHADER, `
      precision highp float;
      varying vec2 v_position;
      uniform sampler2D u_image;
      uniform sampler2D u_mask;
      uniform sampler2D u_aoi;
      uniform vec2 u_viewport;
      uniform vec2 u_center;
      uniform vec2 u_size;
      uniform float u_overlay;
      void main() {
        vec3 background = vec3(.0706, .1686, .2078);
        vec2 uv = (v_position * u_viewport - u_center) / u_size + .5;
        if (uv.x < 0. || uv.x > 1. || uv.y < 0. || uv.y > 1.) {
          gl_FragColor = vec4(background, 1.);
        } else {
          vec4 base = texture2D(u_image, uv);
          vec4 mask = texture2D(u_mask, uv);
          vec3 color = mix(background, base.rgb, base.a);
          color = mix(color, mask.rgb, mask.a * u_overlay * .45);
          vec4 aoi = texture2D(u_aoi, uv);
          color = mix(color, aoi.rgb, aoi.a);
          gl_FragColor = vec4(color, 1.);
        }
      }`);
    this.program = gl.createProgram();
    gl.attachShader(this.program, vertex);
    gl.attachShader(this.program, fragment);
    gl.linkProgram(this.program);
    if (!gl.getProgramParameter(this.program, gl.LINK_STATUS)) throw new Error('WebGL program failed');
    gl.useProgram(this.program);
    const buffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(this.program, 'a_position');
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
    this.uniforms = Object.fromEntries(['image', 'mask', 'aoi', 'viewport', 'center', 'size', 'overlay']
      .map(name => [name, gl.getUniformLocation(this.program, `u_${name}`)]));
  }

  uploadTexture(name, image) {
    const gl = this.gl;
    if (this.textures[name]) gl.deleteTexture(this.textures[name]);
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    const filter = ['mask', 'aoi'].includes(name) ? gl.NEAREST : gl.LINEAR;
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
    this.textures[name] = texture;
  }

  async load(baseUrl, ground = false) {
    const entries = await Promise.all(['height', 'surface', 'mask', 'aoi', ...(ground ? ['ground'] : [])].map(name => new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve([name, img]);
      img.onerror = () => reject(new Error('پیش‌نمایش دریافت نشد؛ خروجی‌ها همچنان قابل دریافت هستند.'));
      img.src = `${baseUrl}/${name}.png`;
    })));
    Object.assign(this.images, Object.fromEntries(entries));
    if (this.gl) for (const [key, img] of entries) this.uploadTexture(key, img);
    this.canvas.hidden = false;
    this.reset();
  }

  reset() { this.zoom = 1; this.pan = [0, 0]; this.draw(); }
  async loadInput(kind, url, aspect, isCurrent) {
    const img = await new Promise((resolve, reject) => {
      const picture = new Image();
      picture.onload = () => resolve(picture);
      picture.onerror = () => reject(new Error('پیش‌نمایش ورودی دریافت نشد.'));
      picture.src = url;
    });
    if (!isCurrent()) return;
    const key = 'input_' + kind;
    this.images[key] = img;
    this.inputAspects[key] = aspect;
    if (this.gl) this.uploadTexture(key, img);
    this.canvas.hidden = false;
  }
  zoomBy(factor, point) {
    const next = Math.max(.5, Math.min(32, this.zoom * factor));
    if (point) {
      const rect = this.canvas.getBoundingClientRect();
      this.pan = this.pan.map((pan, axis) => {
        const coordinate = point[axis] - (axis === 0 ? rect.width : rect.height) / 2;
        return coordinate - (coordinate - pan) * next / this.zoom;
      });
    }
    this.zoom = next;
    this.draw();
  }

  setLayer(layer) { this.layer = layer; this.draw(); }
  setOverlay(show) { this.overlay = show; this.draw(); }
  draw() {
    if (this.frame !== null) return;
    this.frame = requestAnimationFrame(() => { this.frame = null; this.render(); });
  }

  render() {
    const image = this.images[this.layer];
    if (!image) return;
    const rect = this.canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const maxBuffer = this.gl ? this.gl.getParameter(this.gl.MAX_RENDERBUFFER_SIZE) : 8192;
    const ratio = Math.min(window.devicePixelRatio || 1, maxBuffer / rect.width, maxBuffer / rect.height);
    const width = Math.round(rect.width * ratio), height = Math.round(rect.height * ratio);
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
    const input = this.layer.startsWith('input_');
    const imageWidth = image.height * (this.inputAspects[this.layer] || image.width / image.height);
    const scale = Math.min(rect.width / imageWidth, rect.height / image.height) * .88 * this.zoom;
    const size = [imageWidth * scale, image.height * scale];
    const center = [rect.width / 2 + this.pan[0], rect.height / 2 + this.pan[1]];
    const overlay = !input && this.overlay && this.layer !== 'mask';
    if (this.gl) {
      const gl = this.gl;
      if (gl.isContextLost()) return;
      gl.viewport(0, 0, width, height);
      gl.useProgram(this.program);
      gl.uniform2f(this.uniforms.viewport, rect.width, rect.height);
      gl.uniform2f(this.uniforms.center, ...center);
      gl.uniform2f(this.uniforms.size, ...size);
      gl.uniform1f(this.uniforms.overlay, overlay ? 1 : 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, this.textures[this.layer]);
      gl.uniform1i(this.uniforms.image, 0);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, input ? this.textures.empty : this.textures.mask);
      gl.uniform1i(this.uniforms.mask, 1);
      gl.activeTexture(gl.TEXTURE2);
      gl.bindTexture(gl.TEXTURE_2D, input ? this.textures.empty : this.textures.aoi);
      gl.uniform1i(this.uniforms.aoi, 2);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    } else {
      const ctx = this.ctx;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.fillStyle = '#122b35';
      ctx.fillRect(0, 0, rect.width, rect.height);
      ctx.imageSmoothingEnabled = this.layer !== 'mask';
      const args = [center[0] - size[0] / 2, center[1] - size[1] / 2, ...size];
      ctx.drawImage(image, ...args);
      if (overlay) {
        ctx.imageSmoothingEnabled = false;
        ctx.globalAlpha = .45;
        ctx.drawImage(this.images.mask, ...args);
        ctx.globalAlpha = 1;
      }
      ctx.imageSmoothingEnabled = false;
      if (!input && this.images.aoi) ctx.drawImage(this.images.aoi, ...args);
    }
  }
}
