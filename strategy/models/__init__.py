from strategy.models.ou_reversion import OUReversionModel
from strategy.models.vwap_reversion import VWAPReversionModel
from strategy.models.trend_cont import TrendContinuationModel
from strategy.models.afternoon_momentum import AfternoonMomentumModel
from strategy.models.or_reversion import ORReversionModel
from strategy.models.pd_level_reversion import PDLevelReversionModel
from strategy.models.ou_lunch import OULunchZoneModel
from strategy.models.vwap_scalper import VWAPBandScalperModel
from strategy.models.opening_drive import OpeningDriveModel

ALL_MODELS = [
    OUReversionModel,
    PDLevelReversionModel,
    VWAPReversionModel,
    ORReversionModel,
    OULunchZoneModel,
    VWAPBandScalperModel,
    OpeningDriveModel,
    TrendContinuationModel,
    AfternoonMomentumModel,
]
