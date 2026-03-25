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
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False
    logger.warning("ForestEngine deps ausentes (yfinance/sklearn/ta). Modo passthrough ativo.")

MODEL_PATH = os.environ.get("FOREST_MODEL_PATH", "/tmp/rf_model.joblib")
SCALER_PATH = os.environ.get("FOREST_SCALER_PATH", "/tmp/rf_scaler.joblib")


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

    def __init__(self):
        self.model: RandomForestClassifier | None = None
        self.scaler: StandardScaler | None = None
        self._load_if_exists()

    def _load_if_exists(self):
        if not _DEPS_OK:
            return
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            self.model = joblib.load(MODEL_PATH)
            self.scaler = joblib.load(SCALER_PATH)
            logger.info("RandomForest carregado do disco.")

    def train(self, symbols: list[str], years: int = 5) -> bool:
        if not _DEPS_OK:
            return False
        logger.info(f"Treinando RandomForest com {years} anos de histórico: {symbols}")
        frames = []
        for sym in symbols:
            try:
                df = yf.download(sym, period=f"{years}y", interval="1d", progress=False, auto_adjust=True)
                if len(df) < 200:
                    logger.warning(f"Histórico insuficiente para {sym}: {len(df)} candles")
                    continue
                df = _build_features(df)
                frames.append(df)
            except Exception as e:
                logger.error(f"Erro ao baixar {sym}: {e}")

        if not frames:
            logger.error("Nenhum dado histórico disponível para treino.")
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
        joblib.dump(self.model, MODEL_PATH)
        joblib.dump(self.scaler, SCALER_PATH)
        logger.info(f"RandomForest treinado com {len(data)} amostras. Salvo em disco.")
        return True

    def predict(self, feature_vector: np.ndarray) -> dict:
        """Retorna {'signal': 'BUY'|'SELL'|'HOLD', 'confidence': float}"""
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
            logger.error(f"Erro na predição do forest: {e}")
            return {"signal": "HOLD", "confidence": 0.0, "source": "error"}

    def is_ready(self) -> bool:
        return self.model is not None and self.scaler is not None
