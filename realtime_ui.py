import threading
import tkinter as tk
from datetime import datetime

from agents.kite_live import create_kite_agent

REFRESH_MS = 500


class LiveMarketDashboard:
    def __init__(self, root, agent):
        self.root = root
        self.agent = agent
        self.root.title("Intraday Options Live Dashboard")
        self.root.geometry("900x650")
        self.widgets = {}

        self._build_header()
        self._build_symbol_panels()
        self._build_status_panel()
        self._schedule_refresh()

    def _build_header(self):
        header = tk.Label(
            self.root,
            text="Intraday Options Live Market Dashboard",
            font=("Segoe UI", 18, "bold"),
            pady=12,
        )
        header.pack()

    def _build_symbol_panels(self):
        container = tk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        for symbol in self.agent.symbols:
            frame = tk.LabelFrame(container, text=symbol, padx=10, pady=10)
            frame.pack(fill=tk.X, expand=False, padx=8, pady=6)

            labels = {}
            for label_text in [
                "Connection",
                "Last Price",
                "EMA 9",
                "VWAP",
                "Buyers Ratio",
                "Signal",
                "Probability",
                "Depth Snapshot",
                "Updated",
            ]:
                row = tk.Frame(frame)
                row.pack(fill=tk.X, expand=True, pady=2)
                left = tk.Label(row, text=f"{label_text}:", width=16, anchor="w")
                left.pack(side=tk.LEFT)
                right = tk.Label(row, text="—", anchor="w")
                right.pack(side=tk.LEFT, fill=tk.X, expand=True)
                labels[label_text] = right

            self.widgets[symbol] = labels

    def _build_status_panel(self):
        footer = tk.Frame(self.root, pady=12)
        footer.pack(fill=tk.X, padx=12, pady=6)
        self.widgets["status_message"] = tk.Label(
            footer,
            text="Waiting for Kite websocket to connect...",
            anchor="w",
            justify=tk.LEFT,
        )
        self.widgets["status_message"].pack(fill=tk.X)

    def _format_depth(self, depth):
        if not depth:
            return "No depth data"
        buy = depth.get("buy", [])[:3]
        sell = depth.get("sell", [])[:3]
        lines = [
            "BUY: " + ", ".join(str(item.get("quantity", 0)) for item in buy),
            "SELL: " + ", ".join(str(item.get("quantity", 0)) for item in sell),
        ]
        return " | ".join(lines)

    def _update_widgets(self):
        state = self.agent.get_state()
        connected = state.get("connected", False)
        self.widgets["status_message"].config(
            text=(
                f"Connected to Kite websocket. Last update {datetime.utcnow():%H:%M:%S}"
                if connected
                else f"Disconnected. Last error: {state.get('last_error', 'none')}"
            )
        )

        for symbol in self.agent.symbols:
            widget_set = self.widgets.get(symbol, {})
            symbol_state = state.get(symbol, {})
            widget_set["Connection"].config(text="Connected" if connected else "Disconnected")
            widget_set["Last Price"].config(text=f"{symbol_state.get('last_price'):.2f}" if symbol_state.get('last_price') else "—")
            widget_set["EMA 9"].config(text=f"{symbol_state.get('ema_9'):.2f}" if symbol_state.get('ema_9') else "—")
            widget_set["VWAP"].config(text=f"{symbol_state.get('vwap'):.2f}" if symbol_state.get('vwap') else "—")
            widget_set["Buyers Ratio"].config(text=f"{symbol_state.get('buyers_ratio')}%" if symbol_state.get('buyers_ratio') is not None else "—")
            widget_set["Signal"].config(text=symbol_state.get('signal', "—"))
            widget_set["Probability"].config(text=f"{symbol_state.get('probability'):,.0f}%" if symbol_state.get('probability') is not None else "—")
            widget_set["Depth Snapshot"].config(text=self._format_depth(symbol_state.get('depth', {})))
            widget_set["Updated"].config(text=symbol_state.get('timestamp').strftime("%H:%M:%S") if symbol_state.get('timestamp') else "—")

    def _schedule_refresh(self):
        self._update_widgets()
        self.root.after(REFRESH_MS, self._schedule_refresh)


def start_dashboard():
    agent = create_kite_agent()
    agent_thread = threading.Thread(target=agent.start, daemon=True)
    agent_thread.start()

    root = tk.Tk()
    dashboard = LiveMarketDashboard(root, agent)
    root.mainloop()


if __name__ == "__main__":
    start_dashboard()
