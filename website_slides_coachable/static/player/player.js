// Moves and scales the fixed-size Coachable page so that only its playing field shows,
// as large as this frame allows. Everything else on that page, including the name of
// the play, is clipped by the box around it.
(function () {
    "use strict";
    var FIELD = { left: 178, top: 244, width: 844, height: 473 };
    var box = document.getElementById("field");
    var play = document.getElementById("play");

    function fit() {
        var scale = Math.min(window.innerWidth / FIELD.width, window.innerHeight / FIELD.height);
        var width = FIELD.width * scale;
        var height = FIELD.height * scale;
        box.style.width = width + "px";
        box.style.height = height + "px";
        box.style.left = (window.innerWidth - width) / 2 + "px";
        box.style.top = (window.innerHeight - height) / 2 + "px";
        play.style.transform =
            "scale(" + scale + ") translate(" + -FIELD.left + "px," + -FIELD.top + "px)";
    }

    window.addEventListener("resize", fit);
    fit();
})();
