const weatherBlock = document.querySelector('.weather');
const weatherInfo = document.createTextNode(' загрузка...')
weatherBlock.appendChild(weatherInfo)

function getCityWeather() {
    fetch('http://localhost/api/get_city')
    .then(res => res.text())
    .then(data => {
      weatherBlock.textContent = data || "данных о погоде нет"
    }) 
    .catch(err => {
      weatherBlock.textContent = "данных о погоде нет"
      console.error(err)
    })
    
}
getCityWeather()


function calculateCosts() {
    const quantity = parseFloat(document.getElementById('quantity').value);
    const costPerUnit = parseFloat(document.getElementById('costPerUnit').value);
    if (isNaN(quantity) || isNaN(costPerUnit)) {
      alert('Пожалуйста, введите корректные значения.');
      return false;
    }
    const total = quantity * costPerUnit;
    document.getElementById('totalCost').textContent = 'Общая стоимость: ' + total.toFixed(2) + ' руб.';
    return false; // Предотвратить отправку формы
  }

// Получаем все миниатюры изображений в галерее
const images = document.querySelectorAll('.gallery img');

// Получаем модальное окно и его содержимое
const modal = document.getElementById('myModal');
const modalImg = document.getElementById('fullImage');
const spanClose = document.getElementsByClassName('close')[0];

// Назначаем обработчик клика для каждой миниатюры
images.forEach(img => {
  img.onclick = () => {
    modal.style.display = "block";
    modalImg.src = img.src;
    modalImg.alt = img.alt;
  }
});

// Обработчик для закрытия модального окна по клику на крестик
spanClose.onclick = () => {
  modal.style.display = "none";
}

// Закрытие при клике вне изображения
window.onclick = (event) => {
  if (event.target == modal) {
    modal.style.display = "none";
  }
}



