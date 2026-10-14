(() => {
  'use strict';
  const byId = (id) => document.getElementById(id);
  const textElement = (tag, value) => {
    const element = document.createElement(tag);
    element.textContent = value;
    return element;
  };

  fetch('/research-evidence.json', {cache:'no-store'})
    .then((response) => {
      if (!response.ok) throw new Error('Evidence unavailable');
      return response.json();
    })
    .then((data) => {
      const analysis = data.analysis;
      if (data.schema_version !== 2 || !Array.isArray(analysis?.rows) || analysis.rows.length !== 4) throw new Error('Incomplete evidence');
      const tableRows = document.createDocumentFragment();
      for (const row of analysis.rows) {
        const score = row.metrics?.['1']?.pass_power_k;
        if (!Number.isFinite(score) || score < 0 || score > 1) throw new Error('Invalid result');
        const tr = document.createElement('tr');
        const label = textElement('th', row.model_label);
        label.scope = 'row';
        tr.append(label, textElement('td', row.domain === 'retail' ? 'Retail' : 'Airline'), textElement('td', `${row.task_count} × ${row.trials_per_task}`), textElement('td', `${(score * 100).toFixed(2)}%`));
        tableRows.append(tr);
      }
      byId('pilot-status').textContent = data.status;
      byId('pilot-description').textContent = data.description;
      byId('file-count').textContent = String(analysis.released_file_count);
      byId('trajectory-count').textContent = Number(analysis.released_trajectory_count).toLocaleString('en-US');
      byId('provider-call-count').textContent = String(analysis.provider_calls_made);
      byId('author-results').replaceChildren(tableRows);
      byId('generated').textContent = `Evidence summary · ${data.prepared_date_utc}`;
      const checks = document.createDocumentFragment();
      for (const scope of data.native_offline_checks.scope) {
        const item = document.createElement('li');
        item.append(textElement('strong', scope));
        checks.append(item);
      }
      byId('offline-checks').replaceChildren(checks);
    })
    .catch(() => {
      byId('pilot-status').textContent = 'Evidence export unavailable';
      byId('pilot-description').textContent = 'No result should be inferred from a missing or incomplete evidence file. The synthetic interactive demo remains available.';
      byId('author-results').replaceChildren();
      ['file-count','trajectory-count','provider-call-count'].forEach((id) => { byId(id).textContent = '—'; });
    });
})();
