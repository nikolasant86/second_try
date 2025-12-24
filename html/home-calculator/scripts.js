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

