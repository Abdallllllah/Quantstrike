document.addEventListener('DOMContentLoaded', () => {
    const dropZone = document.getElementById('dropZone');
    const fileInput = document.getElementById('fileInput');
    const fileList = document.getElementById('fileList');
    const subjectSelect = document.getElementById('subjectSelect');
    const classSelect = document.getElementById('classSelect');
    const docTypeSelect = document.getElementById('docTypeSelect');

    // Load subjects and existing documents
    fetchSubjects();
    fetchDocuments();

    // ===== CONTEXT SELECTION =====
    async function fetchSubjects() {
        try {
            const response = await fetch('/api/subjects');
            const data = await response.json();

            subjectSelect.innerHTML = '<option value="">Select a subject...</option>';

            if (data.subjects && data.subjects.length > 0) {
                data.subjects.forEach(subject => {
                    const option = document.createElement('option');
                    option.value = subject.id;
                    option.textContent = subject.name;
                    subjectSelect.appendChild(option);
                });
            } else {
                subjectSelect.innerHTML = '<option value="">No subjects available</option>';
            }
        } catch (err) {
            console.error('Failed to load subjects:', err);
            subjectSelect.innerHTML = '<option value="">Error loading subjects</option>';
        }
    }

    async function fetchClasses(subjectId) {
        classSelect.disabled = true;
        classSelect.innerHTML = '<option value="">Loading classes...</option>';

        try {
            const response = await fetch(`/api/classes/${subjectId}`);
            const data = await response.json();

            classSelect.innerHTML = '<option value="">Select a class...</option>';

            if (data.classes && data.classes.length > 0) {
                data.classes.forEach(cls => {
                    const option = document.createElement('option');
                    option.value = cls.id;
                    option.textContent = cls.name;
                    classSelect.appendChild(option);
                });
                classSelect.disabled = false;
            } else {
                classSelect.innerHTML = '<option value="">No classes available</option>';
            }
        } catch (err) {
            console.error('Failed to load classes:', err);
            classSelect.innerHTML = '<option value="">Error loading classes</option>';
        }
    }

    // Update classes when subject changes
    subjectSelect.addEventListener('change', (e) => {
        const subjectId = e.target.value;
        if (subjectId) {
            fetchClasses(subjectId);
        } else {
            classSelect.innerHTML = '<option value="">Select a subject first</option>';
            classSelect.disabled = true;
        }
    });

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

    // ===== DRAG & DROP =====
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

    function highlight() {
        dropZone.classList.add('highlight');
    }

    function unhighlight() {
        dropZone.classList.remove('highlight');
    }

    dropZone.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
        const files = e.dataTransfer.files;
        handleFiles(files);
    }

    // Click to browse
    dropZone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => handleFiles(fileInput.files));

    function handleFiles(files) {
        [...files].forEach(file => {
            if (file.type === 'application/pdf') {
                uploadFile(file);
            } else {
                alert('Only PDF files are allowed');
            }
        });
    }

    // ===== FILE UI =====
    function createFileItemUI(file, isExisting = false) {
        const item = document.createElement('div');
        item.className = 'file-item';

        const info = document.createElement('div');
        info.className = 'file-info';

        const name = document.createElement('span');
        name.className = 'file-name';
        name.textContent = file.name;

        const size = document.createElement('span');
        size.className = 'file-size';
        size.textContent = file.size ? formatFileSize(file.size) : 'Uploaded';

        info.appendChild(name);
        info.appendChild(size);

        const statusEl = document.createElement('div');
        statusEl.className = 'file-status';

        if (isExisting) {
            statusEl.innerHTML = '<i class="fa-solid fa-check"></i>';
            statusEl.classList.add('success');
            // Add delete button for existing files
            setTimeout(() => addDeleteButton(item, file.name), 100);
        } else {
            statusEl.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
        }

        item.appendChild(info);
        item.appendChild(statusEl);

        return { element: item, statusEl };
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    function addDeleteButton(item, filename) {
        const deleteBtn = document.createElement('button');
        deleteBtn.className = 'file-delete-btn';
        deleteBtn.innerHTML = '<i class="fa-solid fa-trash"></i>';
        deleteBtn.onclick = async (e) => {
            e.stopPropagation();
            if (confirm(`Delete ${filename}?`)) {
                try {
                    const response = await fetch(`/api/documents/${encodeURIComponent(filename)}`, {
                        method: 'DELETE'
                    });
                    if (response.ok) {
                        item.remove();
                    } else {
                        alert('Failed to delete file');
                    }
                } catch (err) {
                    console.error('Delete error:', err);
                    alert('Failed to delete file');
                }
            }
        };
        item.appendChild(deleteBtn);
    }

    // ===== UPLOAD =====
    async function uploadFile(file) {
        const ui = createFileItemUI(file);
        fileList.appendChild(ui.element);

        const formData = new FormData();
        formData.append('file', file);

        // Add context if selected
        const subjectId = subjectSelect.value;
        const classId = classSelect.value;
        const docType = docTypeSelect.value;

        if (subjectId) formData.append('subject_id', subjectId);
        if (classId) formData.append('class_id', classId);
        if (docType) formData.append('doc_type', docType);

        try {
            const response = await fetch('/api/upload', {
                method: 'POST',
                body: formData
            });

            const result = await response.json();

            if (response.ok) {
                ui.statusEl.className = 'file-status success';
                ui.statusEl.innerHTML = '<i class="fa-solid fa-check"></i>';

                // Add ingestion indicator if context was provided
                if (subjectId && classId) {
                    // Trigger ingestion
                    try {
                        const ingestResponse = await fetch('/api/ingest', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                filename: file.name,
                                subject_id: subjectId,
                                class_id: classId,
                                doc_type: docType
                            })
                        });
                        if (ingestResponse.ok) {
                            console.log('Document ingested successfully');
                        }
                    } catch (ingestErr) {
                        console.error('Ingestion failed:', ingestErr);
                    }
                }

                // Add delete button after upload
                setTimeout(() => {
                    addDeleteButton(ui.element, file.name);
                }, 500);
            } else {
                ui.statusEl.className = 'file-status error';
                ui.statusEl.innerHTML = '<i class="fa-solid fa-exclamation"></i>';
                console.error('Upload failed:', result);
            }
        } catch (err) {
            console.error('Upload error:', err);
            ui.statusEl.className = 'file-status error';
            ui.statusEl.innerHTML = '<i class="fa-solid fa-exclamation"></i>';
        }
    }
});
