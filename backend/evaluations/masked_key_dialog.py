"""Local masked credential dialog; no credential persistence or network IO."""

from pydantic import ValidationError
from offerpilot_api.agent_runtime.byok import KeyRequest
from secure_key_input import UnsafeKeyInputError


def _build_dialog():
    import tkinter as tk
    from tkinter import ttk
    window = tk.Tk()
    window.withdraw()
    window.title("OfferPilot：安全输入 DeepSeek Key")
    window.resizable(False, False)
    window.attributes("-topmost", True)
    frame = ttk.Frame(window, padding=22)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="只在本机弹窗输入新 Key", font=("Microsoft YaHei", 13, "bold")).pack(anchor="w")
    ttk.Label(frame, text="支持 Ctrl+V 粘贴；字符遮蔽。Key 不写文件、环境变量或日志。\n"
              "仅一份合成 PDF、最多一次请求、输出 4096 tokens、预算 1 元。",
              padding=(0, 10)).pack(anchor="w")
    entry = ttk.Entry(frame, width=58, show="*")
    entry.pack(fill="x", pady=(4, 10))
    status = tk.StringVar(value="请仅粘贴 Key 本身，不带引号、逗号、空格或换行。")
    ttk.Label(frame, textvariable=status).pack(anchor="w")
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(18, 0))
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    window.geometry(f"+{max(0, (window.winfo_screenwidth()-width)//2)}+{max(0, (window.winfo_screenheight()-height)//2)}")
    return window, entry, status, buttons


def read_api_key_dialog():
    result, failures = [], [0]
    window = None
    try:
        from tkinter import ttk
        window, entry, status, buttons = _build_dialog()
        if entry.cget("show") != "*":
            raise RuntimeError("Masked input unavailable")

        def cancel():
            entry.delete(0, "end")
            window.destroy()

        def accept(_event=None):
            try:
                # Reuse the current production Schema/SecretStr validation.
                credential = KeyRequest(api_key=entry.get()).api_key
            except ValidationError:
                failures[0] += 1
                entry.delete(0, "end")
                status.set("格式检查失败，请重新复制 Key 本身。" if failures[0] < 3 else "安全停止。")
                if failures[0] >= 3:
                    cancel()
                return
            result.append(credential.get_secret_value())
            entry.delete(0, "end")
            window.destroy()

        def safe_callback_failure(*_args):
            # Tk's default callback handler prints exception text; never use it.
            result.clear()
            try:
                entry.delete(0, "end")
            except Exception:
                pass
            try:
                window.destroy()
            except Exception:
                try:
                    window.withdraw()
                    window.quit()
                except Exception:
                    pass

        window.report_callback_exception = safe_callback_failure
        ttk.Button(buttons, text="取消", command=cancel).pack(side="left")
        ttk.Button(buttons, text="继续这一次评测", command=accept).pack(side="right")
        window.protocol("WM_DELETE_WINDOW", cancel)
        window.bind("<Return>", accept)
        window.bind("<Escape>", lambda _event: cancel())
        window.after(300000, cancel)
        window.deiconify()
        window.lift()
        entry.focus_force()
        window.mainloop()
    except Exception:
        result.clear()
        if window is not None:
            try:
                window.destroy()
            except Exception:
                pass
    if not result:
        raise UnsafeKeyInputError("invalid_key" if failures[0] >= 3 else "secure_input_failed")
    return result.pop()


def verify_mask_and_cancel():
    """Free native-widget verification: no Key, clipboard or Provider access."""
    window, entry, _status, _buttons = _build_dialog()
    try:
        assert entry.cget("show") == "*"
        entry.insert(0, "nonsecret-local-mask-verification")
        assert entry.get() == "nonsecret-local-mask-verification"
        entry.delete(0, "end")
        assert entry.get() == ""
    finally:
        window.destroy()
    # Exercise real dialog cancellation; replace only its constructor to arrange
    # an automatic user-equivalent close before there is any credential value.
    original = globals()["_build_dialog"]

    def auto_cancel_dialog():
        values = original()
        values[0].after(0, lambda: values[0].event_generate("<Escape>"))
        values[0].after(100, lambda: values[0].destroy())
        return values

    globals()["_build_dialog"] = auto_cancel_dialog
    try:
        try:
            read_api_key_dialog()
        except UnsafeKeyInputError as error:
            assert error.code == "secure_input_failed"
        else:
            raise AssertionError("Cancellation unexpectedly returned a Key")
    finally:
        globals()["_build_dialog"] = original
    return {"masked_native_entry": True, "cleared_entry": True,
            "cancel_returns_no_key": True, "provider_calls": 0}
