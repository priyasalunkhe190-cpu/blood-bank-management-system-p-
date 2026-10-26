# Prefer numpy if available; fallback to a tiny pure-python implementation if not.
try:
    import numpy as np  # type: ignore
    _NUMPY_AVAILABLE = True
except Exception:
    np = None  # type: ignore
    _NUMPY_AVAILABLE = False

# NOTE:

# This project requests pandas + scikit-learn + numpy.
# If pandas installation fails on the user's environment, we still keep the app running
# with a fallback simple Linear Regression implemented using numpy only.

try:
    import pandas as pd  # type: ignore
    from sklearn.linear_model import LinearRegression  # type: ignore
    SKLEARN_AVAILABLE = True
except Exception:
    pd = None
    LinearRegression = None
    SKLEARN_AVAILABLE = False


BLOOD_GROUPS = [
    "A+", "A-", "B+", "B-", "O+", "O-", "AB+", "AB-"
]


def build_sample_dataset():
    """Create a simple beginner-friendly sample dataset."""
    rng = np.random.default_rng(42)

    rows = []
    base = {
        "O+": 90,
        "A+": 70,
        "B+": 55,
        "AB+": 35,
        "O-": 45,
        "A-": 40,
        "B-": 30,
        "AB-": 20,
    }

    for bg in BLOOD_GROUPS:
        for day in range(1, 6):
            noise = rng.normal(0, 6)
            req = max(5, base[bg] + noise + (day - 3) * 1.5)
            rows.append({"blood_group": bg, "day_idx": day, "request_count": req})

    if SKLEARN_AVAILABLE and pd is not None:
        return pd.DataFrame(rows)
    return rows


def train_demand_model(df):
    if SKLEARN_AVAILABLE and LinearRegression is not None and pd is not None:
        df = df.copy()
        df["bg_idx"] = df["blood_group"].apply(lambda x: BLOOD_GROUPS.index(x))
        X = df[["bg_idx", "day_idx"]].values
        y = df["request_count"].values
        model = LinearRegression()
        model.fit(X, y)
        return model

    # Fallback: numpy-only linear regression (least squares)
    # Model: y = w0 + w1*bg_idx + w2*day_idx
    bg_idx = np.array([BLOOD_GROUPS.index(r["blood_group"]) for r in df], dtype=float)
    day_idx = np.array([r["day_idx"] for r in df], dtype=float)
    y = np.array([r["request_count"] for r in df], dtype=float)

    X = np.column_stack([
        np.ones_like(bg_idx),
        bg_idx,
        day_idx,
    ])

    # Solve for w in least squares
    w, *_ = np.linalg.lstsq(X, y, rcond=None)

    def _predict(X_pred):
        # X_pred: shape (n,2) => bg_idx, day_idx
        X2 = np.column_stack([np.ones(X_pred.shape[0]), X_pred[:, 0], X_pred[:, 1]])
        return X2 @ w

    # Return an object with predict method
    class _Model:
        def predict(self, X_pred):
            return _predict(X_pred)

    return _Model()


def predict_next_demand(model, blood_group: str, day_idx: int = 6) -> float:
    if blood_group not in BLOOD_GROUPS:
        raise ValueError("Unknown blood group")

    bg_idx = BLOOD_GROUPS.index(blood_group)
    X_pred = np.array([[bg_idx, float(day_idx)]], dtype=float)
    pred = float(model.predict(X_pred)[0])
    return round(pred, 2)


def get_trained_model():
    df = build_sample_dataset()
    return train_demand_model(df)


