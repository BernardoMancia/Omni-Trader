import os
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger("ForestEngine")

try:
    import yfinance as yf
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    import joblib
    import ta
    import requests as _requests
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False
    logger.warning("ForestEngine deps ausentes (yfinance/sklearn/ta). Modo passthrough ativo.")


def _get_yf_session():
    session = _requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })
    return session


def _build_features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"].squeeze()
    high = df["High"].squeeze()
    low = df["Low"].squeeze()
    volume = df["Volume"].squeeze()

    df["rsi"] = ta.momentum.RSIIndicator(close).rsi()
    macd = ta.trend.MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    bb = ta.volatility.BollingerBands(close)
    df["bb_high"] = bb.bollinger_hband()
    df["bb_low"] = bb.bollinger_lband()
    df["bb_pct"] = bb.bollinger_pband()
    df["ema_20"] = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df["ema_50"] = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df["atr"] = ta.volatility.AverageTrueRange(high, low, close).average_true_range()
    df["vol_ratio"] = volume / volume.rolling(20).mean()
    df["returns"] = close.pct_change()
    df["target"] = np.where(close.shift(-1) > close, 1, np.where(close.shift(-1) < close, -1, 0))
    return df.dropna()


class ForestEngine:
    FEATURE_COLS = [
        "rsi", "macd", "macd_signal", "bb_pct", "ema_20", "ema_50",
        "atr", "vol_ratio", "returns",
    ]

    def __init__(self, model_prefix: str = "us"):
        self.model: RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        self._prefix = model_prefix
        self._model_path = f"/tmp/rf_model_{model_prefix}.joblib"
        self._scaler_path = f"/tmp/rf_scaler_{model_prefix}.joblib"
        self._load_if_exists()

    def _load_if_exists(self):
        if not _DEPS_OK:
            return
        if os.path.exists(self._model_path) and os.path.exists(self._scaler_path):
            self.model = joblib.load(self._model_path)
            self.scaler = joblib.load(self._scaler_path)
            logger.info(f"RandomForest [{self._prefix.upper()}] carregado do disco.")

    def train(self, symbols: list[str], years: int = 5, data_map: dict[str, pd.DataFrame] | None = None) -> bool:
        if not _DEPS_OK:
            return False

        logger.info(f"Treinando RF [{self._prefix.upper()}] com {years} anos: {len(symbols)} ativos")
        frames = []

        for sym in symbols:
            df = None
            if data_map and sym in data_map:
                df = data_map[sym]
                if df is not None and not df.empty:
                    logger.debug(f"Usando dados do DB para {sym} ({len(df)} registros)")

            if df is None or df.empty:
                for attempt in range(3):
                    try:
                        logger.info(f"Baixando historico yfinance para {sym} (tentativa {attempt + 1}/3)...")
                        import time as _time
                        _time.sleep(2 + attempt * 3)
                        df = yf.download(sym, period=f"{years}y", interval="1d", progress=False, auto_adjust=True, session=_get_yf_session())
                        if df is not None and not df.empty:
                            break
                    except Exception as e:
                        logger.error(f"Erro ao baixar {sym} via yfinance: {e}")
                        if attempt < 2:
                            _time.sleep(5)

            if df is not None and len(df) >= 200:
                df = _build_features(df)
                frames.append(df)
                logger.info(f"\u2705 {sym}: {len(df)} amostras carregadas")
            else:
                logger.warning(f"\u26a0\ufe0f Historico insuficiente ou falha para {sym}")

        if not frames:
            logger.error(f"Nenhum dado disponivel para treino [{self._prefix.upper()}].")
            return False

        data = pd.concat(frames).dropna()
        X = data[self.FEATURE_COLS].values
        y = data["target"].values

        self.scaler = StandardScaler()
        X_scaled = self.scaler.fit_transform(X)
        self.model = RandomForestClassifier(
            n_estimators=200,
            max_depth=10,
            min_samples_split=20,
            n_jobs=-1,
            class_weight="balanced",
            random_state=42,
        )
        self.model.fit(X_scaled, y)

        train_acc = self.model.score(X_scaled, y)
        importances = self.model.feature_importances_
        top_idx = np.argsort(importances)[-3:][::-1]
        top_feats = ", ".join([f"{self.FEATURE_COLS[i]}={importances[i]:.3f}" for i in top_idx])

        joblib.dump(self.model, self._model_path)
        joblib.dump(self.scaler, self._scaler_path)
        logger.info(f"RF [{self._prefix.upper()}] treinado: {len(data)} amostras | acc={train_acc:.2%} | top: {top_feats}")
        return True

    def predict(self, feature_vector: np.ndarray) -> dict:
        if not _DEPS_OK or self.model is None or self.scaler is None:
            return {"signal": "HOLD", "confidence": 0.0, "source": "passthrough"}
        try:
            vec = self.scaler.transform(feature_vector.reshape(1, -1))
            proba = self.model.predict_proba(vec)[0]
            classes = self.model.classes_
            label_map = {-1: "SELL", 0: "HOLD", 1: "BUY"}
            idx = int(np.argmax(proba))
            signal = label_map.get(classes[idx], "HOLD")
            confidence = float(proba[idx])
            return {"signal": signal, "confidence": round(confidence, 4), "source": "forest"}
        except Exception as e:
            logger.error(f"Erro na predicao do forest [{self._prefix.upper()}]: {e}")
            return {"signal": "HOLD", "confidence": 0.0, "source": "error"}

    def is_ready(self) -> bool:
        return self.model is not None and self.scaler is not None
