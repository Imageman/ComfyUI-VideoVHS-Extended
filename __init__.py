"""Independent VideoHelperSuite extension; resolve VHS only when the node executes."""

from .video_save import VHSVideoSaveExtended

NODE_CLASS_MAPPINGS = {"VHSE_VideoSave": VHSVideoSaveExtended}
NODE_DISPLAY_NAME_MAPPINGS = {"VHSE_VideoSave": "Video Save Extended (VHS)"}
WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
