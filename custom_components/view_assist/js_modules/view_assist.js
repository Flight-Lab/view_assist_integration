const version = "1.0.5"
const TIMEOUT_ERROR = "SELECTTREE-TIMEOUT";

export async function await_element(el, hard = false) {
  if (el.localName?.includes("-"))
    await customElements.whenDefined(el.localName);
  if (el.updateComplete) await el.updateComplete;
  if (hard) {
    if (el.pageRendered) await el.pageRendered;
    if (el._panelState) {
      let rounds = 0;
      while (el._panelState !== "loaded" && rounds++ < 5)
        await new Promise((r) => setTimeout(r, 100));
    }
  }
}

async function _selectTree(root, path, all = false) {
  let el = [root];
  if (typeof path === "string") {
    path = path.split(/(\$| )/);
  }
  while (path[path.length - 1] === "") path.pop();
  for (const [i, p] of path.entries()) {
    const e = el[0];
    if (!e) return null;

    if (!p.trim().length) continue;

    await_element(e);
    el = p === "$" ? [e.shadowRoot] : e.querySelectorAll(p);
  }
  return all ? el : el[0];
}

export async function selectTree(root, path, all = false, timeout = 10000) {
  return Promise.race([
    _selectTree(root, path, all),
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(TIMEOUT_ERROR)), timeout)
    ),
  ]).catch((err) => {
    if (!err.message || err.message !== TIMEOUT_ERROR) throw err;
    return null;
  });
}

export async function hass_base_el() {
  await Promise.race([
    customElements.whenDefined("home-assistant"),
    customElements.whenDefined("hc-main"),
  ]);

  const element = customElements.get("home-assistant")
    ? "home-assistant"
    : "hc-main";

  while (!document.querySelector(element))
    await new Promise((r) => window.setTimeout(r, 100));
  return document.querySelector(element);
}

export async function hass() {
  const base = await hass_base_el();
  while (!base.hass) await new Promise((r) => window.setTimeout(r, 100));
  return base.hass;
}

function strftime(sFormat, date) {
  if (!(date instanceof Date)) date = new Date();
  var nDay = date.getDay(),
    nDate = date.getDate(),
    nMonth = date.getMonth(),
    nYear = date.getFullYear(),
    nHour = date.getHours(),
    aDays = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'],
    aMonths = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'],
    aDayCount = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334],
    isLeapYear = function() {
      if ((nYear&3)!==0) return false;
      return nYear%100!==0 || nYear%400===0;
    },
    getThursday = function() {
      var target = new Date(date);
      target.setDate(nDate - ((nDay+6)%7) + 3);
      return target;
    },
    zeroPad = function(nNum, nPad) {
      return ('' + (Math.pow(10, nPad) + nNum)).slice(1);
    };
  return sFormat.replace(/%[a-z]/gi, function(sMatch) {
    return {
      '%a': aDays[nDay].slice(0,3),
      '%A': aDays[nDay],
      '%b': aMonths[nMonth].slice(0,3),
      '%B': aMonths[nMonth],
      '%c': date.toUTCString(),
      '%C': Math.floor(nYear/100),
      '%d': zeroPad(nDate, 2),
      '%e': nDate,
      '%F': date.toISOString().slice(0,10),
      '%G': getThursday().getFullYear(),
      '%g': ('' + getThursday().getFullYear()).slice(2),
      '%H': zeroPad(nHour, 2),
      '%I': zeroPad((nHour+11)%12 + 1, 2),
      '%j': zeroPad(aDayCount[nMonth] + nDate + ((nMonth>1 && isLeapYear()) ? 1 : 0), 3),
      '%k': '' + nHour,
      '%l': (nHour+11)%12 + 1,
      '%m': zeroPad(nMonth + 1, 2),
      '%M': zeroPad(date.getMinutes(), 2),
      '%p': (nHour<12) ? 'AM' : 'PM',
      '%P': (nHour<12) ? 'am' : 'pm',
      '%s': Math.round(date.getTime()/1000),
      '%S': zeroPad(date.getSeconds(), 2),
      '%u': nDay || 7,
      '%V': (function() {
              var target = getThursday(),
                n1stThu = target.valueOf();
              target.setMonth(0, 1);
              var nJan1 = target.getDay();
              if (nJan1!==4) target.setMonth(0, 1 + ((4-nJan1)+7)%7);
              return zeroPad(1 + Math.ceil((n1stThu-target)/604800000), 2);
            })(),
      '%w': '' + nDay,
      '%x': date.toLocaleDateString(),
      '%X': date.toLocaleTimeString(),
      '%y': ('' + nYear).slice(2),
      '%Y': nYear,
      '%z': date.toTimeString().replace(/.+GMT([+-]\d+).+/, '$1'),
      '%Z': date.toTimeString().replace(/.+\((.+?)\)$/, '$1')
    }[sMatch] || sMatch;
  });
}

class Clock extends HTMLElement {
  static observedAttributes = ["server_time", "format"];

  constructor() {
    super();
    this.server_time = true;
  }

  connectedCallback() {
    const shadow = this.shadowRoot || this.attachShadow({ mode: 'open' });
    // Create span
    this.shadowRoot.innerHTML = '';
    const el = document.createElement("div");
    el.setAttribute("class", "clock");
    shadow.appendChild(el);

    if (this.hasAttribute("server_time")) this.server_time = this.getAttribute("server_time");

    this.run_clock(el);
  }

  display_time(el) {

    const dt_now = new Date();
    var format = this.getAttribute("format") ? this.getAttribute("format") : '%H:%M'

    if (this.server_time) {
      el.textContent = strftime(format, new Date(dt_now.getTime() + window.viewassist.server_time_delta));
    } else {
      el.textContent = strftime(format,dt_now);
    }
  }

  run_clock(el) {
    var t = this;
    t.display_time(el);
    const x = setInterval(function () {
      t.display_time(el);
    }, 1000);
  }
}

class CountdownTimer extends HTMLElement {
  static observedAttributes = ["expires", "server_time", "show_negative", "no_timer_text", "expired_text"];

  constructor() {
    super();
    this.expires = 0;
    this.server_time = true;
    this.show_negative = true;
    this.expired_text = '';
    this.no_timer_text = '';
    this.interval_timer = null;
  }

  connectedCallback() {
    const shadow = this.shadowRoot || this.attachShadow({ mode: 'open' });
    // Create span
    this.shadowRoot.innerHTML = '';
    const el = document.createElement("div");
    el.setAttribute("class", "countdown");
    shadow.appendChild(el);



    this.expires = this.getAttribute("expires");
    if (this.hasAttribute("server_time")) this.server_time = this.getAttribute("server_time");
    if (this.hasAttribute("show_negative")) this.show_negative = this.getAttribute("show_negative");
    if (this.hasAttribute("no_timer_text")) this.no_timer_text = this.getAttribute("no_timer_text");
    if (this.hasAttribute("expired_text")) this.expired_text = this.getAttribute("expired_text");

    this.start_timer(el);
  }

  disconnectedCallback() {
    clearInterval(this.interval_timer);
  }

  display_countdown(el) {
    let dt_now = new Date();
    if (this.server_time) {
      // Use now plus server time delta to compare expiry to
      dt_now = new Date(dt_now.getTime() + window.viewassist.server_time_delta);
    }

    const expire = new Date(this.expires).getTime();

    // Find the distance between now and the count down date
    let distance = (expire - dt_now) / 1000;
    let disp_distance = Math.abs(Math.round(distance))

    // Time calculations for days, hours, minutes and seconds
    let days = Math.floor(disp_distance / (60 * 60 * 24));
    let hours = String(Math.floor((disp_distance % (60 * 60 * 24)) / (60 * 60))).padStart(2,'0');
    let minutes = String(Math.floor((disp_distance % (60 * 60)) / (60))).padStart(2,'0');
    let seconds = String(Math.floor(disp_distance % (60))).padStart(2,'0');

    // Display the result in the element
    let sign = Math.round(distance) < 0 ? '-':'';
    if (days) {
      el.textContent = sign + days + "d " + hours + ":" + minutes + ":" + seconds;
    } else {
      el.textContent = sign + hours + ":" + minutes + ":" + seconds;
    }
    return distance
  }

  start_timer(el) {
    if (this.expires != 0) {
      var t = this;
      t.display_countdown(el)
      this.interval_timer = setInterval(function () {
        var distance = t.display_countdown(el);
        if (!t.show_negative && distance < 0) {
          clearInterval(this.interval_timer);
          el.textContent = t.expired_text;
        }
      }, 500);
    } else {
      if (typeof x !== 'undefined') { clearInterval(this.interval_timer) };
      el.textContent = this.no_timer_text;
    }
  }
}

class VAData {
  constructor() {
    this.config;
    this.server_time_delta = 0;
  }
}

class ViewAssist {
  constructor(hass) {
    this._hass = hass
    this.variables = new VAData();
    this.connect_ws = true;
    this.connected = false;
    this.initializeWhenReady();
  }

  async hide_header(enabled) {
    let elMain = await selectTree(
      document.body,
      "home-assistant $ home-assistant-main $ partial-panel-resolver ha-panel-lovelace $ hui-root $"
    )

    await selectTree(
      elMain, "hui-view-container"
    ).then((el) => {
      enabled ? el?.style.setProperty("padding-top", "0px") : el?.style.removeProperty("padding-top")
    });

    await selectTree(
      elMain, ".header"
    ).then((el) => {
      enabled ? el?.style.setProperty("display", "none"): el?.style.removeProperty("display")
    });
  }

  async hide_sidebar(enabled) {
    let elMain = await selectTree(
      document.body,
      "home-assistant $ home-assistant-main"
    )

    enabled ? elMain?.style?.setProperty("--mdc-drawer-width", "0px") : elMain?.style?.removeProperty("--mdc-drawer-width");

    await selectTree(
      elMain, "$ partial-panel-resolver"
    ).then((el) => {
      enabled ? el.style.setProperty("--mdc-top-app-bar-width", "100% !important") : el.style.removeProperty("--mdc-top-app-bar-width")
    });

    await selectTree(
      elMain, "$ ha-drawer ha-sidebar"
    ).then((el) => {
      enabled ? el.style.setProperty("display", "none !important") : el.style.removeProperty("display")
    });

    await selectTree(
      elMain, "$ partial-panel-resolver ha-panel-lovelace $ hui-root $ ha-menu-button"
    ).then((el) => {
      enabled ? el.style.setProperty("display", "none") : el.style.removeProperty("display")
    });

    // Hide white line on left
    await selectTree(
      elMain, "$ ha-drawer $ aside"
    ).then((el) => {
      enabled ? el.style.setProperty("display", "none") : el.style.removeProperty("display");
    });

  }

  async initializeWhenReady(attempts = 0) {
    if (attempts > 50) {
      console.log("Failed to initialize after 50 attempts");
      return;
    }

    try {
      // Connect to server websocket
      if (this.connect_ws) {
        await this.connect("connected")
        window.addEventListener("connection-status", (ev) => this.connect(ev.detail, 2000));
      }

      window.addEventListener("location-changed", () => {
        //console.log("Location changed, hiding sections");
        this.hide_sections();
      });

      // Update time delta and set 5 min refresh interval
      await this.set_time_delta();
      var t = this;
      const delta = setInterval(function () {
        t.set_time_delta();
      }, 300 * 1000);


      const bc = await Promise.resolve(customElements.whenDefined("button-card"))
      if (!bc) {
        throw new Error("No button-card element");
      }

      customElements.define("viewassist-countdown", CountdownTimer)
      customElements.define("viewassist-clock", Clock)

    } catch (e) {
      console.log("Initialization retry:", e.message);
      setTimeout(() => this.initializeWhenReady(attempts + 1), 100);
    }
  }

  async hide_sections() {
    // Hide header and sidebar
    if (!this.variables.config?.mimic_device) {
      await this.hide_header(this.variables.config?.hide_header);
      await this.hide_sidebar(this.variables.config?.hide_sidebar);
      setTimeout(() => {
        this.hide_header(this.variables.config?.hide_header);
      }, 10);
    }
  }

  async connect(state, delay = 0) {
    // Subscribe to server updates

    if (delay != 0) await new Promise(r => setTimeout(r, delay));

    if (state === "connected") {
      try {

        const conn = (await hass()).connection;
        conn.subscribeMessage((msg) => this.incoming_message(msg), {
          type: "view_assist/connect",
          browser_id: localStorage.getItem("browser_mod-browser-id"),
        })
        this.connected = true;
        console.log("ViewAssist connected to server")
      } catch {
        console.log("Unable to connect to server")
      }
    } else {
      this.connected = false;
    }
  }

  async incoming_message(msg) {
    // Handle incomming messages from the server
    let event = msg["event"];
    let payload = msg["payload"];
    console.log("event:", event, " -> ", payload);

    if (event == "connection" || event == "config_update" || event == "registered") {
      await this.process_config(event, payload);
    }
    if (event == "timer_update") {
      this.variables.config.timers = payload
    }
    if (event == "navigate") {
      if (payload["variables"]) this.variables.navigation = payload["variables"];
      this.browser_navigate(payload["path"]);
    }
  }

  async process_config(event, payload) {
    let reload = false;
    const old_config = this.variables?.config

    if (event == "config_update" && payload.home != this.variables.config.home) {
      console.log("Changed home view")
      reload = true;
    }

    // Entity id and mimic device
    if (payload.entity_id && payload.entity_id != localStorage.getItem("view_assist_sensor")) {
      console.log("Set view assist sensor value")
      localStorage.setItem("view_assist_sensor", payload.entity_id);
      localStorage.setItem("view_assist_mimic_device", payload.mimic_device);
      reload = true;
    }

    // Set variables to payload
    this.variables.config = payload

    if (!payload.mimic_device) {

      // On update of config, go to default page
      if (event == "connection" || reload) {
        this.browser_navigate(payload.home);
      } else if (event == "registered") {
          location.reload();
      } else {
        window.dispatchEvent(new CustomEvent("location-changed"));
      }
    }
  }

  async set_time_delta() {
    // Get this clients time delta to the server
    if (this.connected) {
      const delta = await this._hass.callWS({
        type: 'view_assist/get_server_time_delta',
        epoch: new Date().getTime()
      })
      this.variables.server_time_delta = delta;
    }
  }

  browser_navigate(path) {
    // Navigate the browser window
    if (!this.variables.config.mimic_device) {
      if (!path) return;
      console.log("Navigating to " + path)
      history.pushState(null, "", path);
      window.dispatchEvent(new CustomEvent("location-changed"));
    }
  }
}

// Initialize when core web components are ready
const ha = await hass();

Promise.all([
  customElements.whenDefined("home-assistant"),
  customElements.whenDefined("hui-view")
]).then(() => {
  console.info(
    `%cVIEW ASSIST ${version} IS INSTALLED
      %cView Assist Entity: ${localStorage.getItem("view_assist_sensor")}
      Is Mimic Device: ${localStorage.getItem("view_assist_mimic_device")}`,
      "color: green; font-weight: bold",
      ""
  );
  window.viewassist = new ViewAssist(ha).variables;
});
