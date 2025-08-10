const API = location.origin.replace(/:\d+$/, ':8000');

const form = document.getElementById('form');
const statusEl = document.getElementById('status');
const gallery = document.getElementById('gallery');

function setStatus(msg) {
  statusEl.textContent = msg;
}

function clearGallery() {
  gallery.innerHTML = '';
}

function addCard(title, src) {
  const card = document.createElement('div');
  card.className = 'card';
  const h = document.createElement('h3');
  h.textContent = title;
  const img = document.createElement('img');
  img.src = API + '/file/' + src;
  img.alt = title;
  card.appendChild(h);
  card.appendChild(img);
  gallery.appendChild(card);
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const city = document.getElementById('city').value.trim();
  const year = parseInt(document.getElementById('year').value, 10);
  if (!city) return;
  clearGallery();
  setStatus('Running analysis. This may take a few minutes...');
  try {
    const resp = await fetch(`${API}/analyze?city=${encodeURIComponent(city)}&year=${year}`, { method: 'POST' });
    if (!resp.ok) throw new Error(await resp.text());
    const data = await resp.json();
    setStatus(`Done for ${data.city} (${data.year}).`);

    const assets = data.assets;
    const ordered = [
      'lst.png','ndvi.png','albedo.png','impervious.png','building_density.png','population.png','ntl.png','water_distance.png','elevation.png','lulc.png','threshold_exceedance.png','preliminary_hotspots.png','validated_hotspots.png'
    ];
    for (const key of ordered) {
      if (assets[key]) addCard(key.replace('.png','').replace('_',' ').toUpperCase(), assets[key]);
    }

    if (assets['spatial_stats.json']) {
      const link = document.createElement('a');
      link.href = API + '/file/' + assets['spatial_stats.json'];
      link.textContent = 'Download spatial statistics JSON';
      link.target = '_blank';
      gallery.appendChild(link);
    }
  } catch (err) {
    console.error(err);
    setStatus('Error: ' + err.message);
  }
});