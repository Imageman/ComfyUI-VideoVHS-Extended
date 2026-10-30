"""Independent VideoHelperSuite extension; resolve VHS only when the node executes."""

from .video_save import VHSVideoSaveExtended
from .yue2_auto_instrumental import VHSXYuE2AutoInstrumental

NODE_CLASS_MAPPINGS = {
    "VHSE_VideoSave": VHSVideoSaveExtended,
    "VHSX_YuE2AutoInstrumental": VHSXYuE2AutoInstrumental,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "VHSE_VideoSave": "Video Save Extended (VHS)",
    "VHSX_YuE2AutoInstrumental": "YuE2 Auto Instrumental (VHSX)",
}
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
