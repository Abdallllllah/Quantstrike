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
        // Validate context selection
        const subjectId = subjectSelect.value;
        const classId = classSelect.value;

        if (!subjectId || !classId) {
            alert('Please select a Subject and Class before uploading.');
            return;
        }

        ([...files]).forEach(file => uploadFile(file, subjectId, classId));
    }

    function uploadFile(file, subjectId, classId) {
        // Validate PDF
        if (file.type !== 'application/pdf') {
            alert('Only PDF files are allowed');
            return;
        }

        // Create UI element
        const ui = createFileItemUI(file);
        fileList.prepend(ui.element);

        // Upload with context
        const formData = new FormData();
        formData.append('file', file);
        formData.append('subject_id', subjectId);
        formData.append('class_id', classId);
        formData.append('doc_type', docTypeSelect.value);

        fetch('/api/ingest', {
            method: 'POST',
            body: formData
        })
            .then(response => {
                if (!response.ok) throw new Error('Upload failed');
                return response.json();
            })
            .then(data => {
                updateUIStatus(ui, 'success', file.name);
                // Show success message with context
                const subjectName = subjectSelect.options[subjectSelect.selectedIndex].text;
                const className = classSelect.options[classSelect.selectedIndex].text;
                console.log(`Uploaded ${file.name} to ${subjectName} / ${className}`);
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

    // ===== EXAM QUESTION LOOKUP =====
    const examRefInput = document.getElementById('examRefInput');
    const examSearchBtn = document.getElementById('examSearchBtn');
    const examResultsArea = document.getElementById('examResultsArea');

    if (examSearchBtn) {
        examSearchBtn.addEventListener('click', performExamLookup);
        examRefInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') performExamLookup();
        });
    }

    function performExamLookup() {
        const reference = examRefInput.value.trim();
        if (!reference) return;

        examResultsArea.innerHTML = '<div class="loading-spinner"><i class="fa-solid fa-spinner fa-spin"></i> Looking up question...</div>';

        fetch('/api/exam-question', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reference: reference })
        })
            .then(response => response.json())
            .then(data => {
                examResultsArea.innerHTML = '';

                if (data.error) {
                    examResultsArea.innerHTML = `<p style="color:var(--error-color)">${data.error}</p>`;
                    return;
                }

                const resultCard = document.createElement('div');
                resultCard.className = 'result-card exam-result';
                resultCard.innerHTML = `
                    <div class="result-header"><i class="fa-solid fa-file-lines"></i> ${data.reference}</div>
                    <div class="result-content">${data.result}</div>
                `;
                examResultsArea.appendChild(resultCard);

                // Show sources
                if (data.sources && data.sources.length > 0) {
                    const sourcesDiv = document.createElement('div');
                    sourcesDiv.style.marginTop = '1rem';
                    data.sources.forEach(source => {
                        const badge = document.createElement('span');
                        badge.className = 'result-source';
                        badge.innerHTML = `<i class="fa-regular fa-file-pdf"></i> ${source}`;
                        sourcesDiv.appendChild(badge);
                    });
                    examResultsArea.appendChild(sourcesDiv);
                }
            })
            .catch(err => {
                console.error(err);
                examResultsArea.innerHTML = '<p style="color:var(--error-color)">Error looking up question.</p>';
            });
    }

    // ===== PRACTICE QUESTIONS =====
    const practiceTopicInput = document.getElementById('practiceTopicInput');
    const difficultySelect = document.getElementById('difficultySelect');
    const countSelect = document.getElementById('countSelect');
    const generateQuestionsBtn = document.getElementById('generateQuestionsBtn');
    const practiceResultsArea = document.getElementById('practiceResultsArea');

    if (generateQuestionsBtn) {
        generateQuestionsBtn.addEventListener('click', generatePracticeQuestions);
        practiceTopicInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') generatePracticeQuestions();
        });
    }

    function generatePracticeQuestions() {
        const topic = practiceTopicInput.value.trim();
        if (!topic) return;

        const difficulty = difficultySelect.value;
        const count = parseInt(countSelect.value);

        practiceResultsArea.innerHTML = '<div class="loading-spinner"><i class="fa-solid fa-spinner fa-spin"></i> Generating questions...</div>';

        fetch('/api/generate-questions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic, difficulty, count })
        })
            .then(response => response.json())
            .then(data => {
                practiceResultsArea.innerHTML = '';

                if (data.error) {
                    practiceResultsArea.innerHTML = `<p style="color:var(--error-color)">${data.error}</p>`;
                    return;
                }

                const resultCard = document.createElement('div');
                resultCard.className = 'result-card practice-result';
                resultCard.innerHTML = `
                    <div class="result-header"><i class="fa-solid fa-brain"></i> Practice Questions: ${data.topic} (${data.difficulty})</div>
                    <div class="result-content">${data.questions}</div>
                `;
                practiceResultsArea.appendChild(resultCard);
            })
            .catch(err => {
                console.error(err);
                practiceResultsArea.innerHTML = '<p style="color:var(--error-color)">Error generating questions.</p>';
            });
    }

    // ===== ANSWER MARKING =====
    const markingQuestionInput = document.getElementById('markingQuestionInput');
    const studentAnswerInput = document.getElementById('studentAnswerInput');
    const maxMarksInput = document.getElementById('maxMarksInput');
    const markAnswerBtn = document.getElementById('markAnswerBtn');
    const markingResultsArea = document.getElementById('markingResultsArea');

    if (markAnswerBtn) {
        markAnswerBtn.addEventListener('click', markStudentAnswer);
    }

    function markStudentAnswer() {
        const question = markingQuestionInput.value.trim();
        const studentAnswer = studentAnswerInput.value.trim();
        const maxMarks = parseInt(maxMarksInput.value) || 5;

        if (!question) {
            alert('Please enter the question.');
            return;
        }
        if (!studentAnswer) {
            alert('Please enter your answer.');
            return;
        }

        markingResultsArea.innerHTML = '<div class="loading-spinner"><i class="fa-solid fa-spinner fa-spin"></i> Marking your answer...</div>';

        fetch('/api/mark-answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, student_answer: studentAnswer, max_marks: maxMarks })
        })
            .then(response => response.json())
            .then(data => {
                markingResultsArea.innerHTML = '';

                if (data.error) {
                    markingResultsArea.innerHTML = `<p style="color:var(--error-color)">${data.error}</p>`;
                    return;
                }

                const resultCard = document.createElement('div');
                resultCard.className = 'result-card marking-result';
                resultCard.innerHTML = `
                    <div class="result-header"><i class="fa-solid fa-check-double"></i> Marking Result</div>
                    <div class="result-content">${data.marking_result}</div>
                `;
                markingResultsArea.appendChild(resultCard);
            })
            .catch(err => {
                console.error(err);
                markingResultsArea.innerHTML = '<p style="color:var(--error-color)">Error marking answer.</p>';
            });
    }
});
