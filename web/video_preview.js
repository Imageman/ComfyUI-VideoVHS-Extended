import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

app.registerExtension({
    name: "VHSE.VideoPreview",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "VHSE_VideoSave") return;

        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const result = onCreated?.apply(this, arguments);
            const video = document.createElement("video");
            video.controls = true;
            video.loop = true;
            video.preload = "metadata";
            video.style.width = "100%";
            video.style.display = "none";
            const widget = this.addDOMWidget("videopreview", "preview", video, { serialize: false });
            widget.computeSize = (width) => [width, video.videoWidth
                ? Math.round(Math.max(0, this.size[0] - 20) * video.videoHeight / video.videoWidth) + 8 : 0];
            video.addEventListener("loadedmetadata", () => {
                const width = this.size[0];
                const height = Math.max(this.size[1], this.computeSize()[1]);
                this.setSize([width, height]);
                this.setDirtyCanvas(true, true);
            });
            this.vhseVideo = video;
            return result;
        };

        const onExecuted = nodeType.prototype.onExecuted;
        nodeType.prototype.onExecuted = function (message) {
            const result = onExecuted?.apply(this, arguments);
            const preview = message?.gifs?.[0];
            if (preview && this.vhseVideo) {
                const params = new URLSearchParams({
                    filename: preview.filename,
                    subfolder: preview.subfolder || "",
                    type: preview.type || "output",
                });
                this.vhseVideo.src = api.apiURL(`/view?${params}`);
                this.vhseVideo.style.display = "block";
            }
            return result;
        };

        const onRemoved = nodeType.prototype.onRemoved;
        nodeType.prototype.onRemoved = function () {
            if (this.vhseVideo) {
                this.vhseVideo.pause();
                this.vhseVideo.removeAttribute("src");
                this.vhseVideo.load();
            }
            return onRemoved?.apply(this, arguments);
        };
    },
});
