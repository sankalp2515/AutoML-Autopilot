from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler

def build_preprocessor():
    return ColumnTransformer([("numeric", StandardScaler(), ["signal", "aux"]),
        ("category", OneHotEncoder(sparse=False, handle_unknown="ignore"), ["employment_type"])])
