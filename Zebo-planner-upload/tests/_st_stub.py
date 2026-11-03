"""A recording stand-in for the `streamlit` (and `altair`) modules, used to execute app scripts in tests
where Streamlit is not installed. It exercises OUR script logic; it does not test Streamlit rendering."""
import runpy
import sys
import types


class Rerun(Exception):
    pass


class Stop(Exception):
    pass


class State(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class Recorder:
    """Accepts any st.* call. Widgets return values from `inputs` (by key, then label) or sensible defaults."""

    def __init__(self, inputs, press, log, state):
        self._inputs, self._press, self._log, self._state = inputs, press, log, state

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __call__(self, *a, **k):
        return self._child()

    def _child(self):
        return Recorder(self._inputs, self._press, self._log, self._state)

    def _value(self, label, key, default):
        if key is not None and key in self._inputs:
            return self._inputs[key]
        if label in self._inputs:
            return self._inputs[label]
        if key is not None and key in self._state:
            return self._state[key]
        return default

    def __getattr__(self, name):
        def call(*args, **kwargs):
            label = str(args[0]) if args else str(kwargs.get("label", ""))
            key = kwargs.get("key")
            self._log.append((name, label))
            if name == "tabs":
                return [self._child() for _ in args[0]]
            if name == "columns":
                n = args[0] if isinstance(args[0], int) else len(args[0])
                return [self._child() for _ in range(n)]
            if name in ("button", "form_submit_button", "download_button"):
                return any(p == label or (p.endswith("*") and label.startswith(p[:-1])) for p in self._press) \
                    or (key is not None and key in self._press)
            if name in ("selectbox", "radio"):
                opts = list(args[1]) if len(args) > 1 else list(kwargs.get("options", []))
                v = self._value(label, key, None)
                if v not in opts:
                    v = opts[kwargs.get("index", 0) or 0] if opts else None
                if key is not None:
                    self._state[key] = v
                return v
            if name == "multiselect":
                return self._value(label, key, [])
            if name in ("checkbox", "toggle"):
                return bool(self._value(label, key, False))
            if name == "slider":
                return self._value(label, key, args[3] if len(args) > 3 else kwargs.get("value", 1))
            if name == "number_input":
                return self._value(label, key, kwargs.get("value", args[3] if len(args) > 3 else 0))
            if name in ("text_input", "text_area"):
                return self._value(label, key, "")
            if name == "chat_input":
                return self._inputs.get(key or "chat_input")
            if name == "dataframe":
                rows = self._inputs.get(f"select:{key}", []) if key else []
                return types.SimpleNamespace(selection=types.SimpleNamespace(rows=rows))
            if name == "rerun":
                raise Rerun()
            if name == "stop":
                raise Stop()
            if name in ("cache_resource", "cache_data"):
                if args and callable(args[0]):
                    return args[0]
                return lambda f: f
            return self._child()
        return call


def run(app_path, inputs=None, press=(), state=None):
    """Run the script once. Returns (log, state, rerun_requested)."""
    log = []
    state = state if state is not None else State()
    root = Recorder(inputs or {}, press, log, state)
    st = types.ModuleType("streamlit")
    st.__getattr__ = root.__getattr__
    st.session_state = state
    st.column_config = types.SimpleNamespace(NumberColumn=lambda *a, **k: None, ProgressColumn=lambda *a, **k: None, TextColumn=lambda *a, **k: None)
    st.sidebar = root._child()
    alt = types.ModuleType("altair")
    alt.__getattr__ = lambda name: (lambda *a, **k: _Chain())
    saved = {m: sys.modules.get(m) for m in ("streamlit", "altair")}
    sys.modules["streamlit"], sys.modules["altair"] = st, alt
    rerun = False
    try:
        try:
            runpy.run_path(str(app_path), run_name="__main__")
        except Rerun:
            rerun = True
    finally:
        for m, v in saved.items():
            if v is None:
                sys.modules.pop(m, None)
            else:
                sys.modules[m] = v
    return log, state, rerun


class _Chain:
    def __getattr__(self, name):
        return lambda *a, **k: self
