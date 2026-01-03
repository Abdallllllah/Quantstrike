document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');

    // Load existing documents
    fetchDocuments();

    function fetchDocuments() {
        fetch('/api/documents')
            .then(response => response.json())
            .then(data => {
                if (data.files) {
                    data.files.forEach(filename => {
                        const ui = createFileItemUI({ name: filename }, true);
                        fileList.appendChild(ui.element);
                    });
                }
            })
            .catch(err => console.error("Failed to load documents", err));
    }

    // Drag & Drop events
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, unhighlight, false);
    });

    function highlight(e) {
        dropZone.classList.add('dragover');
    }

    function unhighlight(e) {
        dropZone.classList.remove('dragover');
    }

    dropZone.addEventListener('drop', handleDrop, false);

    // Click to upload
    dropZone.addEventListener('click', () => {
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    }

    function handleFiles(files) {
        ([...files]).forEach(uploadFile);
    }

    function uploadFile(file) {
        // Validate PDF
        if (file.type !== 'application/pdf') {
            alert('Only PDF files are allowed');
            return;
        }

        // Create UI element
        const ui = createFileItemUI(file);
        fileList.prepend(ui.element);

        // Upload
        const url = '/api/upload';
        const formData = new FormData();
        formData.append('file', file);

        fetch(url, {
            method: 'POST',
            body: formData
        })
            .then(response => {
                if (!response.ok) throw new Error('Upload failed');
                return response.json();
            })
            .then(data => {
                updateUIStatus(ui, 'success', file.name);
            })
            .catch(error => {
                console.error('Error:', error);
                updateUIStatus(ui, 'error');
            });
    }

    function createFileItemUI(file, isExisting = false) {
        const div = document.createElement('div');
        div.className = 'file-item';
        div.innerHTML = `
            <i class="fa-regular fa-file-pdf file-icon"></i>
            <div class="file-info">
                <span class="file-name">${file.name}</span>
                <div class="progress-bar"><div class="fill" style="width: ${isExisting ? '100%' : '0%'}"></div></div>
            </div>
            <div class="file-status">
                ${isExisting ? '<i class="fa-solid fa-check"></i>' : '<i class="fa-solid fa-spinner fa-spin"></i>'}
            </div>
        `;

        // Simulate progress for new uploads only
        if (!isExisting) {
            const fill = div.querySelector('.fill');
            setTimeout(() => fill.style.width = '100%', 500);
        } else {
            div.querySelector('.file-status').classList.add('success');
        }

        // Add Delete functionality if it's an existing file or after upload success
        if (isExisting) {
            addDeleteButton(div, file.name);
        }

        return {
            element: div,
            statusEl: div.querySelector('.file-status')
        };
    }

    function addDeleteButton(element, filename) {
        const statusEl = element.querySelector('.file-status');
        statusEl.innerHTML = '<i class="fa-regular fa-trash-can delete-btn" title="Delete"></i>';

        const deleteBtn = statusEl.querySelector('.delete-btn');
        deleteBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (confirm(`Are you sure you want to delete ${filename}?`)) {
                deleteFile(filename, element);
            }
        });
    }

    function deleteFile(filename, element) {
        fetch(`/api/documents/${filename}`, {
            method: 'DELETE'
        })
            .then(response => {
                if (response.ok) {
                    element.remove();
                } else {
                    alert('Failed to delete file');
                }
            })
            .catch(err => console.error(err));
    }

    function updateUIStatus(ui, status, filename = null) {
        if (status === 'success') {
            ui.statusEl.className = 'file-status success';
            ui.statusEl.innerHTML = '<i class="fa-solid fa-check"></i>';
            if (filename) {
                // Convert checkmark to delete button after a moment
                setTimeout(() => {
                    addDeleteButton(ui.element, filename);
                }, 1000);
            }
        } else {
            ui.statusEl.className = 'file-status error';
            ui.statusEl.innerHTML = '<i class="fa-solid fa-exclamation"></i>';
        }
    }

    // Search Functionality
    const queryInput = document.getElementById('queryInput');
    const searchBtn = document.getElementById('searchBtn');
    const resultsArea = document.getElementById('resultsArea');

    if (searchBtn) {
        searchBtn.addEventListener('click', performSearch);
        queryInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performSearch();
        });
    }

    function performSearch() {
        const query = queryInput.value.trim();
        if (!query) return;

        resultsArea.innerHTML = '<div style="text-align:center;"><i class="fa-solid fa-spinner fa-spin"></i> Searching...</div>';

        fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query: query })
        })
            .then(response => response.json())
            .then(data => {
                resultsArea.innerHTML = '';

                if (data.answer) {
                    // Show Answer
                    const answerCard = document.createElement('div');
                    answerCard.className = 'result-card ai-answer';
                    answerCard.innerHTML = `
                    <div class="result-header"><i class="fa-solid fa-robot"></i> AI Answer</div>
                    <div class="result-content markdown-body">${data.answer}</div>
                `;
                    resultsArea.appendChild(answerCard);

                    // Show Sources
                    if (data.sources && data.sources.length > 0) {
                        const sourcesDiv = document.createElement('div');
                        sourcesDiv.style.marginTop = '1rem';
                        sourcesDiv.innerHTML = '<div style="color:var(--text-secondary); margin-bottom:0.5rem; font-size:0.9rem;">Sources:</div>';

                        data.sources.forEach(source => {
                            const badger = document.createElement('span');
                            badger.className = 'result-source';
                            badger.innerHTML = `<i class="fa-regular fa-file-pdf"></i> ${source}`;
                            sourcesDiv.appendChild(badger);
                        });
                        resultsArea.appendChild(sourcesDiv);
                    }
                } else if (data.results) {
                    // Fallback to old behavior if endpoint returns results (backward compatibility)
                    if (data.results.length > 0) {
                        data.results.forEach(result => {
                            const card = document.createElement('div');
                            card.className = 'result-card';
                            card.innerHTML = `
                            <span class="result-source"><i class="fa-regular fa-file-pdf"></i> ${result.source}</span>
                            <div class="result-content">${result.content}</div>
                        `;
                            resultsArea.appendChild(card);
                        });
                    } else {
                        resultsArea.innerHTML = '<p class="text-center" style="color:var(--text-secondary)">No relevant documents found.</p>';
                    }
                }
            })
            .catch(err => {
                console.error(err);
                resultsArea.innerHTML = '<p style="color:var(--error-color)">Error performing search.</p>';
            });
    }
});
