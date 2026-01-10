/*
function calculateCosts() {
  const quantity = document.getElementById('quantity').value;
  const costPerUnit = document.getElementById('costPerUnit').value;

  fetch('/calculate', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ quantity, costPerUnit })
  })
  .then(response => response.json())
/*  .then(data => {
    document.getElementById('totalCost').innerText = 'Общая стоимость: ' + data.totalCost.toFixed(2);
  })
  .then(data => {
  document.getElementById('totalCost').innerText = 'Общая стоимость: ' + data.totalCost.toFixed(2);
  })
  .catch(error => {
    document.getElementById('totalCost').innerText = 'Ошибка при вычислении.';
  });
  return false; // отмена отправки формы по умолчанию
}*/



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
