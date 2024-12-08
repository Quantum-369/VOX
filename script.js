let ws;
let isRecording = false;
let mediaRecorder;
let audioChunks = [];
let stream = null;
let audioContext;
let analyser;
let currentAudio = null;
let isPlayingResponse = false;

document.addEventListener('DOMContentLoaded', function() {
    initializeWebSocket();
    initializeUI();
});

function initializeUI() {
    // Show GIF initially
    const gifImage = document.querySelector('.gif-container img');
    gifImage.style.display = 'block';

    // Initialize microphone button
    const recordButton = document.querySelector('.glow-on-hover');
    if (recordButton) {
        recordButton.addEventListener('click', toggleRecording);
    }
}

async function toggleRecording() {
    const recordButton = document.querySelector('.glow-on-hover');
    
    // Stop current audio playback if active
    if (isPlayingResponse && currentAudio) {
        currentAudio.pause();
        currentAudio = null;
        isPlayingResponse = false;
        updateStatus('idle');
    }
    
    if (!isRecording) {
        try {
            stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            startRecording(stream, recordButton);
            ws.send(JSON.stringify({ type: 'start_listening' }));
        } catch (error) {
            console.error('Mic error:', error);
            showErrorMessage('Microphone access error');
        }
    } else {
        stopRecording(recordButton);
    }
}

function startRecording(stream, recordButton) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    source.connect(analyser);
    
    mediaRecorder = new MediaRecorder(stream);
    audioChunks = [];
    
    mediaRecorder.ondataavailable = (event) => audioChunks.push(event.data);
    
    mediaRecorder.onstop = async () => {
        if (audioChunks.length > 0) {
            updateStatus('processing');
            const audioBlob = new Blob(audioChunks, { type: 'audio/wav' });
            sendAudioToServer(audioBlob);
        }
        cleanupRecording();
    };

    mediaRecorder.start();
    isRecording = true;
    recordButton.innerHTML = '<i class="fas fa-stop"></i>';
    recordButton.style.backgroundColor = '#ff4444';
    updateStatus('listening');
}

function stopRecording(recordButton) {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    isRecording = false;
    recordButton.innerHTML = '<i class="fas fa-microphone"></i>';
    recordButton.style.backgroundColor = '#111';
}

function cleanupRecording() {
    if (stream) {
        stream.getTracks().forEach(track => track.stop());
        stream = null;
    }
    if (audioContext) {
        audioContext.close();
        audioContext = null;
        analyser = null;
    }
    audioChunks = [];
}

function sendAudioToServer(audioBlob) {
    const reader = new FileReader();
    reader.readAsArrayBuffer(audioBlob);
    reader.onloadend = () => {
        ws.send(JSON.stringify({
            type: 'audio_data',
            audio: Array.from(new Uint8Array(reader.result))
        }));
    };
}

function initializeWebSocket() {
    ws = new WebSocket('ws://localhost:8000/ws');
    
    ws.onmessage = async (event) => {
        const data = JSON.parse(event.data);
        
        switch(data.type) {
            case 'available_databases':
                handleDatabaseList(data.databases);
                break;
            case 'database_selection':
                handleDatabaseSelection(data);
                if (data.audioData) {
                    await playAudioResponse(data.audioData);
                }
                break;
            case 'no_speech_detected':
                startContinuousListening();
                break;
            case 'transcript':
                updateStatus('processing');
                updateTranscript(data.text, 'User');
                break;
            case 'assistant_response':
                updateTranscript(data.text, 'VOX');
                if (data.audioData) {
                    await playAudioResponse(data.audioData);
                }
                break;
            case 'session_ended':
                updateTranscript(data.message, 'VOX');
                if (data.audioData) {
                    await playAudioResponse(data.audioData);
                }
                updateStatus('idle');
                break;
            case 'error':
                console.error('Server error:', data.message);
                showErrorMessage(data.message);
                updateStatus('idle');
                break;
        }
    };
    
    ws.onclose = () => {
        showErrorMessage('Connection lost. Attempting to reconnect...');
        setTimeout(initializeWebSocket, 5000);
    };
    
    ws.onerror = (error) => {
        showErrorMessage('Connection error occurred');
    };
}

function playAudioResponse(audioData) {
    if (!audioData || !audioData.audio) return;
    
    try {
        updateStatus('speaking');
        const audioBlob = base64ToBlob(audioData.audio, audioData.type);
        const audioUrl = URL.createObjectURL(audioBlob);
        
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
            isPlayingResponse = false;
        }
        
        const audio = new Audio(audioUrl);
        currentAudio = audio;
        isPlayingResponse = true;
        
        audio.onended = () => {
            URL.revokeObjectURL(audioUrl);
            updateStatus('idle');
            startContinuousListening();
            isPlayingResponse = false;
            currentAudio = null;
        };
        
        audio.play().catch(error => {
            console.error('Error playing audio:', error);
            updateStatus('idle');
            isPlayingResponse = false;
            currentAudio = null;
        });
    } catch (error) {
        console.error('Error processing audio:', error);
        showErrorMessage('Error playing audio response');
        updateStatus('idle');
        isPlayingResponse = false;
        currentAudio = null;
    }
}

function updateTranscript(text, speaker) {
    const transcriptContent = document.querySelector('.transcript-box .transcript-content');
    if (transcriptContent && text) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${speaker === 'User' ? 'user-message' : 'vox-message'}`;
        
        const content = document.createElement('div');
        content.className = 'message-content';
        content.textContent = text;
        
        const avatar = document.createElement('div');
        avatar.className = 'message-avatar';
        
        if (speaker !== 'User') {
            const avatarImg = document.createElement('img');
            avatarImg.src = 'assets/VOX.ico';
            avatarImg.alt = 'VOX';
            avatarImg.className = 'vox-icon';
            avatar.appendChild(avatarImg);
        }
        avatar.appendChild(document.createTextNode(speaker === 'User' ? 'You' : 'VOX'));
        
        messageDiv.appendChild(content);
        messageDiv.appendChild(avatar);
        
        transcriptContent.appendChild(messageDiv);
        transcriptContent.scrollTop = transcriptContent.scrollHeight;
    }
}

function handleDatabaseList(databases) {
    updateTranscript('Available databases: ' + databases.join(', '), 'VOX');
}

function handleDatabaseSelection(data) {
    if (data.success) {
        updateTranscript(data.text || `Successfully connected to ${data.selected} database`, 'VOX');
    } else {
        updateTranscript('Failed to connect to database', 'VOX');
        showErrorMessage('Database connection failed');
    }
}

function base64ToBlob(base64, type) {
    try {
        const byteCharacters = atob(base64);
        const byteArrays = [];
        
        for (let offset = 0; offset < byteCharacters.length; offset += 512) {
            const slice = byteCharacters.slice(offset, offset + 512);
            const byteNumbers = new Array(slice.length);
            
            for (let i = 0; i < slice.length; i++) {
                byteNumbers[i] = slice.charCodeAt(i);
            }
            
            const byteArray = new Uint8Array(byteNumbers);
            byteArrays.push(byteArray);
        }
        
        return new Blob(byteArrays, { type: type });
    } catch (error) {
        console.error('Error converting base64 to blob:', error);
        throw error;
    }
}

async function startContinuousListening() {
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const btn = document.querySelector('.glow-on-hover');
        startRecording(stream, btn);
        ws.send(JSON.stringify({ type: 'start_listening' }));
    } catch (error) {
        console.error('Mic error:', error);
        showErrorMessage('Microphone access error');
    }
}

function showErrorMessage(message) {
    const existingError = document.querySelector('.error-message');
    if (existingError) existingError.remove();

    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-message';
    errorDiv.textContent = message;
    document.body.appendChild(errorDiv);
    
    setTimeout(() => errorDiv.remove(), 3000);
}

function updateStatus(status) {
    const indicator = document.querySelector('.status-indicator');
    const textElement = indicator.querySelector('.status-text');
    
    indicator.classList.remove('listening', 'processing', 'speaking');
    
    switch (status) {
        case 'listening':
            indicator.classList.add('active', 'listening');
            textElement.textContent = 'Listening...';
            break;
        case 'processing':
            indicator.classList.add('active', 'processing');
            textElement.textContent = 'Processing...';
            break;
        case 'speaking':
            indicator.classList.add('active', 'speaking');
            textElement.textContent = 'Speaking...';
            break;
        case 'idle':
            indicator.classList.remove('active');
            break;
    }
}

window.addEventListener('beforeunload', () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'end_session' }));
    }
});