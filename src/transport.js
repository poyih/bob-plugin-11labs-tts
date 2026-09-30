// Bob 原生 WebSocket 适配。协议依据：
// https://elevenlabs.io/docs/eleven-api/guides/how-to/websockets/realtime-tdd
// HTTP 与 WebSocket 均返回 Bob 的 response/data/rawData 形态，供 main.js 统一校验。
var config = require("./config.js");

function messageOf(error) {
    return error && error.message ? error.message : String(error);
}

function request(params) {
    var info = config.MODELS[params.body.model_id] || {};
    if (info.transport === "dialogueWebSocket") {
        return requestDialogue(params);
    }
    return $http.request({
        method: "POST",
        url: config.API_BASE + "/text-to-speech/" + encodeURIComponent(params.voiceId) +
            "?output_format=" + encodeURIComponent(params.outputFormat),
        header: { "xi-api-key": params.apiKey, "Content-Type": "application/json" },
        body: params.body,
        timeout: params.timeout
    });
}

function errorResponse(message) {
    var detail = message.detail;
    if (!detail) {
        detail = message.error && typeof message.error === "object" ? message.error : {
            code: typeof message.code === "string" ? message.code :
                (typeof message.error === "string" ? message.error : ""),
            message: message.message || "ElevenLabs 流式合成失败"
        };
    }
    var status = message.status_code;
    var hasHttpStatus = typeof status === "number" && status >= 400 && status < 600;
    return {
        // 500 仅用于兼容 HTTP 响应形态；不要把它显示成服务器实际返回的 HTTP 状态。
        response: { statusCode: hasHttpStatus ? status : 500, syntheticStatus: !hasHttpStatus },
        data: { detail: detail }
    };
}

function requestDialogue(params) {
    return new Promise(function (resolve, reject) {
        var socket = null;
        var timerId = null;
        var settled = false;
        var audio = null;

        function finish(error, response) {
            if (settled) {
                return;
            }
            settled = true;
            if (timerId !== null) {
                try { $timer.invalidate(timerId); } catch (ignored) {}
            }
            if (socket) {
                try { socket.close(); } catch (ignored) {}
            }
            if (error) {
                reject(error);
            } else {
                resolve(response);
            }
        }

        function receive(text) {
            if (settled) {
                return;
            }
            try {
                var message = JSON.parse(text);
                if (!message || typeof message !== "object" || Array.isArray(message)) {
                    throw new Error("响应不是 JSON 对象");
                }
                if (message.error || message.detail) {
                    finish(null, errorResponse(message));
                    return;
                }
                if (message.audio !== undefined && message.audio !== null && message.audio !== "") {
                    // 不能直接拼 base64 字符串：每个分块的 padding 会截断后续音频。
                    var encoded = message.audio;
                    if (typeof encoded !== "string" ||
                        !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded)) {
                        throw new Error("音频分块不是有效的 base64");
                    }
                    var chunk = $data.fromBase64(encoded);
                    if (!chunk || !chunk.toBase64()) {
                        throw new Error("音频分块解码失败");
                    }
                    if (audio) {
                        audio.appendData(chunk);
                    } else {
                        audio = chunk;
                    }
                }
                // 单个 turn 的结束不能代表整个 MP3 编码器已 flush，必须等会话 final。
                if (message.is_final === true) {
                    if (!audio || !audio.toBase64()) {
                        finish({ type: "api", message: "ElevenLabs 返回了空音频" });
                        return;
                    }
                    finish(null, {
                        response: { statusCode: 200, MIMEType: "application/octet-stream" },
                        data: audio,
                        rawData: audio
                    });
                }
            } catch (err) {
                finish({ type: "api", message: "ElevenLabs 流式响应无效：" + messageOf(err) });
            }
        }

        try {
            if (typeof $websocket === "undefined" || typeof $timer === "undefined") {
                finish({ type: "param", message: "当前 Bob 运行环境不支持 v4 Turbo 所需的 WebSocket 和定时器" });
                return;
            }
            var query = "model_id=" + encodeURIComponent(params.body.model_id) +
                "&output_format=" + encodeURIComponent(params.outputFormat);
            if (params.body.language_code) {
                query += "&language_code=" + encodeURIComponent(params.body.language_code);
            }
            socket = $websocket.new({
                url: config.API_BASE.replace(/^https:/, "wss:") + "/text-to-dialogue/stream-input?" + query,
                header: { "xi-api-key": params.apiKey },
                timeoutInterval: params.timeout
            });
            socket.listenOpen(function () {
                if (settled) {
                    return;
                }
                try {
                    var setup = { voices: [params.voiceId] };
                    if (params.body.voice_settings) {
                        setup.voice_settings = params.body.voice_settings;
                    }
                    socket.sendString(JSON.stringify(setup));
                    socket.sendString(JSON.stringify({
                        inputs: [{ text: params.body.text, voice_id: params.voiceId }]
                    }));
                    // 短文本也要强制 flush，不能等到 40 字符 / 8 单词的缓冲阈值。
                    socket.sendString(JSON.stringify({ close_socket: true }));
                } catch (err) {
                    finish({ type: "network", message: "发送 ElevenLabs 流式请求失败：" + messageOf(err) });
                }
            });
            socket.listenReceiveString(function (_, text) { receive(text); });
            socket.listenReceiveData(function (_, data) {
                try {
                    receive(data.toUTF8());
                } catch (err) {
                    finish({ type: "api", message: "ElevenLabs 流式响应无法读取：" + messageOf(err) });
                }
            });
            socket.listenError(function (_, error) {
                finish({ type: "network", message: "ElevenLabs WebSocket 连接失败：" +
                    (error && error.message ? error.message : "未知网络错误") });
            });
            socket.listenClose(function (_, code) {
                finish({ type: "network", message: "ElevenLabs 流式合成提前结束（关闭码 " + code + "），未收到完整音频" });
            });
            timerId = $timer.schedule({
                interval: params.timeout,
                repeats: false,
                handler: function () {
                    finish({ type: "network", message: "ElevenLabs 流式合成超时，请稍后重试" });
                }
            });
            socket.open();
        } catch (err) {
            finish({ type: "network", message: "无法启动 ElevenLabs 流式合成：" + messageOf(err) });
        }
    });
}

exports.request = request;
